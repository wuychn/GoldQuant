"""端到端冒烟：建最小离线库 → 跑 build_panel → 跑 backtest。

验证 r3 三个 P0 修复后入口脚本可运行、不泄露未来。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

CWD = Path(r"D:\workspace\GoldQuant-r3")
sys.path.insert(0, str(CWD))

from quant.data.store import write_daily_raw, write_calendar, write_universe_snapshot
from quant.data.calendar import trading_day_list, to_iso
from quant.data.universe import build_universe_snapshot
from datetime import date


def build_synthetic_store():
    rng = np.random.default_rng(7)
    dates = [to_iso(d) for d in trading_day_list(date(2024, 1, 1), date(2024, 6, 30))]
    codes = [f"{600000 + i:06d}" for i in range(20)] + [f"{300000 + i:06d}" for i in range(5)]
    rows = []
    for code in codes:
        p = 10.0
        for d in dates:
            p = max(p * (1 + rng.normal(0.001, 0.02)), 1.0)
            rows.append({
                "code": code, "date": d, "name": f"股{code[-4:]}",
                "open": p, "high": p * 1.01, "low": p * 0.99, "close": p,
                "pre_close": p, "volume": 1e6, "amount": p * 1e6,
                "turnover_rate": 1.0, "float_mv": 1e10, "total_mv": 2e10,
            })
    daily = pd.DataFrame(rows)
    write_daily_raw(daily)
    write_calendar(dates)
    # 建 universe 快照（合成数据上市天数不足 120，放宽阈值）
    for d in dates[::10]:
        snap = build_universe_snapshot(d, daily=daily, min_list_days=10, min_adv_yi=0.0)
        if not snap.empty:
            write_universe_snapshot(snap)
    print(f"已建离线库: {len(daily)} 行, {len(codes)} 票, {len(dates)} 日")


def test_build_panel_runs():
    from quant.factors.panel_builder import build_panel
    from quant.data.store import read_daily_raw

    dates = [to_iso(d) for d in trading_day_list(date(2024, 3, 1), date(2024, 6, 30))]
    daily = read_daily_raw()
    # 预建评估日快照（合成数据上市天数不足 120，放宽阈值）
    from quant.data.store import write_universe_snapshot
    from quant.data.universe import build_universe_snapshot
    for d in dates:
        snap = build_universe_snapshot(d, daily=daily, min_list_days=10, min_adv_yi=0.0)
        if not snap.empty:
            write_universe_snapshot(snap)
    panel = build_panel(dates, daily=daily, adj=pd.DataFrame(), industries={})
    assert len(panel) > 0, "面板为空"
    # 日期必须统一 ISO
    for r in panel[:50]:
        assert len(r.date) == 10 and r.date[4] == "-", f"日期非 ISO: {r.date}"
    print(f"build_panel OK: {len(panel)} 行, 日期统一 ISO")


def test_backtest_runs():
    from quant.backtest2.engine import run_backtest
    from quant.backtest2.policy import EqualWeightTopN
    from quant.backtest2.metrics import compute_metrics
    from quant.data.store import read_daily_raw

    dates = [to_iso(d) for d in trading_day_list(date(2024, 4, 1), date(2024, 6, 30))]
    daily = read_daily_raw()
    # 用收盘价做简单 alpha
    def alpha_fn(d, rows):
        return {c: float(r["close"]) for c, r in rows.items()}
    broker = run_backtest(daily=daily, dates=dates, alpha_fn=alpha_fn,
                          policy=EqualWeightTopN(n=5), initial_cash=1_000_000)
    m = compute_metrics(broker)
    assert m["n_days"] > 0
    assert len(broker.trades) > 0, "回测零成交"
    print(f"backtest OK: {len(broker.trades)} 笔成交, final_equity={m['final_equity']}, sharpe={m['sharpe']}")


def test_backtest_strict_signals():
    from quant.backtest2.engine import run_backtest, ExitConfig
    from quant.backtest2.policy import EqualWeightTopN
    from quant.data.store import read_daily_raw

    dates = [to_iso(d) for d in trading_day_list(date(2024, 4, 1), date(2024, 6, 30))]
    daily = read_daily_raw()
    def alpha_fn(d, rows):
        return {c: float(r["close"]) for c, r in rows.items()}
    broker = run_backtest(daily=daily, dates=dates, alpha_fn=alpha_fn,
                          policy=EqualWeightTopN(n=5), initial_cash=1_000_000,
                          exit_config=ExitConfig(hard_pct=0.08, max_hold_days=15),
                          strict_signals=True)
    assert len(broker.trades) > 0
    print(f"strict backtest OK: {len(broker.trades)} 笔成交")


if __name__ == "__main__":
    build_synthetic_store()
    test_build_panel_runs()
    test_backtest_runs()
    test_backtest_strict_signals()
    print("\n=== 端到端冒烟全部通过 ===")
