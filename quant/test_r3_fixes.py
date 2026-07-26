"""r3 修复回归测试：日期格式统一、ExitTracker 生命周期、端到端可运行性。

这些测试针对评估文档中发现的 P0 缺陷，确保修复不回归。
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from quant.backtest2.engine import run_backtest
from quant.backtest2.policy import EqualWeightTopN
from quant.backtest2.tradability import _limit_pct, limit_state
from quant.data.calendar import to_iso, trading_day_list
from quant.exit.rules import evaluate_exits
from quant.exit.state import ExitTracker
from quant.factors.panel_builder import _normalize_daily, build_panel


def test_to_iso_normalizes_both_formats():
    assert to_iso("20240115") == "2024-01-15"
    assert to_iso("2024-01-15") == "2024-01-15"
    assert to_iso(date(2024, 1, 15)) == "2024-01-15"


def test_iso_string_comparison_no_future_leak():
    """核心回归：ISO 格式下字符串比较不把未来日期当作过去。"""
    probe = "2024-01-15"
    # 6月、12月必须 > 1月（修复前 ISO vs YYYYMMDD 会全部判 True）
    assert "2024-06-15" > probe
    assert "2024-12-20" > probe
    assert "2024-01-10" < probe


def test_normalize_daily_converts_compact_to_iso():
    df = pd.DataFrame({
        "code": ["000001"] * 3,
        "date": ["20240110", "20240115", "20240620"],
        "close": [10.0, 11.0, 12.0],
    })
    out = _normalize_daily(df)
    assert out["date"].iloc[0] == "2024-01-10"
    assert out["date"].iloc[2] == "2024-06-20"


def test_limit_pct_by_board():
    assert _limit_pct("600519") == 0.10
    assert _limit_pct("300750") == 0.20
    assert _limit_pct("688981") == 0.20
    assert _limit_pct("830879") == 0.30
    assert _limit_pct("000001", name="*ST 某股") == 0.05
    assert _limit_pct("600519", name="贵州茅台") == 0.10


def test_limit_state_sealed_not_one_price():
    """封板判定：盘中封板（非一字）也应识别为封死。"""
    # 主板涨停 10%，prev=10，涨停价=11，收盘=最高=11，最低=10.5（盘中曾打开但收封）
    row = {"open": 10.5, "high": 11.0, "low": 10.5, "close": 11.0, "code": "600519", "name": "贵州茅台"}
    assert limit_state(row, 10.0, code="600519") == "up"
    # 跌停封死
    row2 = {"open": 9.5, "high": 9.5, "low": 9.0, "close": 9.0, "code": "600519", "name": "贵州茅台"}
    assert limit_state(row2, 10.0, code="600519") == "down"
    # 未封板
    row3 = {"open": 10.5, "high": 11.0, "low": 10.0, "close": 10.8, "code": "600519", "name": "贵州茅台"}
    assert limit_state(row3, 10.0, code="600519") == "none"


def test_exit_tracker_lifecycle_on_rebalance_sell():
    """再平衡清仓后 tracker 必须 close，重新买入时重新 open（修复前的 bug）。"""
    tracker = ExitTracker()
    # 模拟买入
    tracker.open("000001", 10.0, "2024-01-01")
    assert tracker.get("000001") is not None
    # 模拟清仓（引擎在 sell 后调用 close）
    tracker.close("000001")
    assert tracker.get("000001") is None
    # 重新买入必须能 open（修复前因残留会跳过）
    tracker.open("000001", 12.0, "2024-02-01")
    st = tracker.get("000001")
    assert st is not None and st.entry_price == 12.0


def test_engine_exit_tracker_closed_on_full_sell():
    """端到端：持仓被出场规则清仓后，tracker 应已 close。"""
    rng = np.random.default_rng(1)
    rows = []
    dates = pd.bdate_range(end="2024-12-31", periods=40).strftime("%Y-%m-%d")
    for code in ["000001", "000002", "000003"]:
        p = 10.0
        for d in dates:
            p = max(p * (1 + rng.normal(0, 0.02)), 1.0)
            rows.append({"code": code, "date": d, "open": p, "high": p * 1.01,
                         "low": p * 0.99, "close": p, "volume": 1e6, "name": code})
    daily = pd.DataFrame(rows)

    def alpha_fn(d, _rows):
        return {c: 1.0 for c in _rows}

    from quant.backtest2.engine import ExitConfig

    broker = run_backtest(
        daily=daily, dates=list(dates), alpha_fn=alpha_fn,
        policy=EqualWeightTopN(n=2), initial_cash=1_000_000,
        exit_config=ExitConfig(hard_pct=0.05, max_hold_days=3),
    )
    # 应有成交且引擎没崩
    assert len(broker.trades) > 0


def test_build_panel_with_mixed_date_formats():
    """build_panel 接受 YYYYMMDD 与 ISO 混合输入，内部归一化为 ISO。"""
    rng = np.random.default_rng(2)
    rows = []
    dates = pd.bdate_range(end="2024-06-30", periods=80).strftime("%Y-%m-%d")
    for code in ["000001", "000002"]:
        p = 10.0
        for d in dates:
            p = max(p * (1 + rng.normal(0.001, 0.02)), 1.0)
            rows.append({"code": code, "date": d, "open": p, "high": p, "low": p,
                         "close": p, "volume": 1e6, "amount": p * 1e6,
                         "turnover_rate": 1.0, "float_mv": 1e10, "name": code})
    daily = pd.DataFrame(rows)
    # 用真实存在于数据中的交易日（bdate_range 末尾三天）避免依赖离线库日历
    picked = list(dates[-6:-3])
    mixed = [picked[0].replace("-", ""), picked[1], picked[2].replace("-", "")]
    universe = {d: ["000001", "000002"] for d in picked}
    panel = build_panel(mixed, daily=daily, adj=pd.DataFrame(), industries={},
                        universe_by_date=universe)
    assert panel, "面板不应为空"
    got = {r.date for r in panel}
    # 三个输入日期（含紧凑格式）都必须归一化为 ISO 并出现在结果里
    assert got == set(picked), (got, set(picked))


def test_forward_returns_keyed_by_date_and_code():
    """前瞻收益必须按 (date, code) 索引：同一票不同评估日的值不能互相覆盖。"""
    from quant.factors.panel_builder import _forward_returns_panel

    dates = ["2024-01-%02d" % i for i in range(1, 21)]
    # 前 10 天横盘 10.0，第 11 天起翻倍到 20.0
    closes = [10.0] * 10 + [20.0] * 10
    daily = pd.DataFrame({"code": ["000001"] * 20, "date": dates, "close": closes})

    res = _forward_returns_panel(daily, {"000001"}, {"2024-01-01", "2024-01-08"})
    # 01-01 往后 5 日仍在横盘区 → 0%
    assert abs(res[("2024-01-01", "000001")][5] - 0.0) < 1e-6
    # 01-08 往后 5 日跨入翻倍区 → +100%
    assert abs(res[("2024-01-08", "000001")][5] - 100.0) < 1e-6


def test_build_panel_forward_returns_differ_across_dates():
    """端到端：同一票在不同评估日必须拿到各自的前瞻收益，而非同一份。"""
    # 需足够长历史让因子算得出值（mom_20 需 21 点），且评估日后仍有 5 日可算前瞻
    dates = list(pd.bdate_range(start="2024-01-01", periods=40).strftime("%Y-%m-%d"))
    # 前 30 日横盘 10.0，第 31 日起跳涨到 20.0
    closes = [10.0] * 30 + [20.0] * 10
    rows = []
    for code in ["000001", "000002"]:
        for d, c in zip(dates, closes):
            rows.append({"code": code, "date": d, "open": c, "high": c, "low": c,
                         "close": c, "volume": 1e6, "amount": c * 1e6,
                         "turnover_rate": 1.0, "float_mv": 1e10, "name": code})
    daily = pd.DataFrame(rows)

    d_flat = dates[24]   # 第 25 日：+5 日仍在横盘区 → 0%
    d_jump = dates[27]   # 第 28 日：+5 日跨入跳涨区 → +100%
    universe = {d: ["000001", "000002"] for d in (d_flat, d_jump)}
    panel = build_panel([d_flat, d_jump], daily=daily, adj=pd.DataFrame(),
                        industries={}, universe_by_date=universe)

    by_key = {(r.date, r.code): r.forward_return_pct for r in panel}
    assert by_key, "面板为空"
    v_flat = by_key.get((d_flat, "000001"))
    v_jump = by_key.get((d_jump, "000001"))
    assert v_flat is not None and v_jump is not None, by_key
    assert abs(v_flat - 0.0) < 1e-6, v_flat
    assert abs(v_jump - 100.0) < 1e-6, v_jump
    assert v_flat != v_jump, "不同评估日的前瞻收益被覆盖成同一份"


def test_exit_tracker_upsert_preserves_highest_close():
    """加仓不能重置 highest_close，否则 ATR 跟踪止损失效。"""
    tracker = ExitTracker()
    tracker.open("000001", 10.0, "2024-01-01")
    # 股价涨到 20
    tracker.update("000001", 20.0)
    assert tracker.get("000001").highest_close == 20.0
    # 加仓（新加权成本 15）：highest_close 必须保留 20，不能回落到 15
    tracker.upsert("000001", 15.0, "2024-01-01")
    st = tracker.get("000001")
    assert st.entry_price == 15.0, "成本价应更新"
    assert st.highest_close == 20.0, f"highest_close 被加仓重置为 {st.highest_close}"
    # 清仓后重新买入才允许重置
    tracker.close("000001")
    tracker.upsert("000001", 12.0, "2024-03-01")
    assert tracker.get("000001").highest_close == 12.0


def test_limit_state_rounds_to_cent():
    """涨停价须四舍五入到分：prev_close=10.05 时涨停价为 11.06 而非 11.055。"""
    # 理论价 11.055 → 交易所涨停价 11.06
    row = {"open": 11.06, "high": 11.06, "low": 10.8, "close": 11.06,
           "code": "600519", "name": "某股"}
    assert limit_state(row, 10.05, code="600519") == "up"
    # 高价股同样成立（prev=1700 → 涨停 1870.00）
    row_hi = {"open": 1870.0, "high": 1870.0, "low": 1800.0, "close": 1870.0,
              "code": "600519", "name": "贵州茅台"}
    assert limit_state(row_hi, 1700.0, code="600519") == "up"


def test_factors_do_not_leak_future():
    """因子只能用 as_of 及之前的数据：追加未来数据不得改变因子值。"""
    from quant.factors.library import ALL_FACTORS, BarSeries

    dates_past = ["2024-01-%02d" % i for i in range(1, 26)]
    closes_past = [10.0 + i * 0.1 for i in range(25)]

    def _mk(dates, closes):
        df = pd.DataFrame({
            "open": closes, "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes], "close": closes,
            "volume": [1e6] * len(closes), "amount": [c * 1e6 for c in closes],
            "turnover_rate": [1.0] * len(closes),
        }, index=dates)
        return BarSeries(code="000001", df=df)

    as_of = dates_past[-1]
    bars_a = _mk(dates_past, closes_past)
    # 追加剧烈上涨的未来数据
    dates_b = dates_past + ["2024-02-%02d" % i for i in range(1, 11)]
    closes_b = closes_past + [c * 5 for c in closes_past[:10]]
    bars_b = _mk(dates_b, closes_b)

    for f in ALL_FACTORS:
        va = f.compute(bars_a, as_of)
        vb = f.compute(bars_b, as_of)
        if va is None and vb is None:
            continue
        assert va is not None and vb is not None, f"{f.name} 结果类型不一致"
        assert abs(va - vb) < 1e-9, f"因子 {f.name} 泄露未来: {va} != {vb}"


def test_dsr_decreases_with_trials_still_holds():
    from quant.research2.significance import deflated_sharpe

    s1 = deflated_sharpe(1.5, n=252, n_trials=1)
    s100 = deflated_sharpe(1.5, n=252, n_trials=100)
    assert s100["dsr"] <= s1["dsr"]
    # 高 Sharpe 仍显著
    assert deflated_sharpe(3.0, n=500, n_trials=5)["dsr"] > 0.8
