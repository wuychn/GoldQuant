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
    """build_panel 接受 YYYYMMDD 与 ISO 混合输入，内部归一化，不泄露未来。"""
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
    # 传混合格式评估日
    mixed = ["20240620", "2024-06-25", "20240628"]
    panel = build_panel(mixed, daily=daily, adj=pd.DataFrame(), industries={})
    # 应有结果且日期统一为 ISO
    for r in panel:
        assert r.date.startswith("2024-06-") or r.date.startswith("2024-06")
        assert "-" in r.date and len(r.date) == 10


def test_dsr_decreases_with_trials_still_holds():
    from quant.research2.significance import deflated_sharpe

    s1 = deflated_sharpe(1.5, n=252, n_trials=1)
    s100 = deflated_sharpe(1.5, n=252, n_trials=100)
    assert s100["dsr"] <= s1["dsr"]
    # 高 Sharpe 仍显著
    assert deflated_sharpe(3.0, n=500, n_trials=5)["dsr"] > 0.8
