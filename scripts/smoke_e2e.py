"""端到端冒烟：在临时目录建最小离线库 → build_panel → backtest。

不会写入真实 ``~/.quant/store``。用法：
    python scripts/smoke_e2e.py
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

CWD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CWD))


def _build_synthetic_daily(dates: list[str]) -> pd.DataFrame:
    rng = np.random.default_rng(7)
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
    return pd.DataFrame(rows)


def run_smoke() -> None:
    from quant.data.calendar import to_iso, trading_day_list
    from quant.data.store import (
        read_daily_raw,
        write_calendar,
        write_daily_raw,
        write_universe_snapshot,
    )
    from quant.data.universe import build_universe_snapshot
    from quant.factors.panel_builder import build_panel
    from quant.backtest2.engine import ExitConfig, run_backtest
    from quant.backtest2.metrics import compute_metrics
    from quant.portfolio2.target import TargetPortfolio

    # 合成日历（不依赖外部网络日历）
    synth_dates = list(pd.bdate_range(start="2024-01-01", end="2024-06-28").strftime("%Y-%m-%d"))

    with tempfile.TemporaryDirectory(prefix="gq-smoke-") as td:
        home = Path(td)
        with patch("quant.store.paths.quant_home", return_value=home), \
             patch("quant.data.store.quant_home", return_value=home):
            # calendar 也指向临时家目录
            with patch("quant.data.calendar.quant_home", return_value=home):
                write_calendar(synth_dates)
                daily = _build_synthetic_daily(synth_dates)
                write_daily_raw(daily)
                print(f"临时库: {home} | {len(daily)} 行, {daily['code'].nunique()} 票, {len(synth_dates)} 日")

                dates = [d for d in synth_dates if d >= "2024-03-01"]
                for d in dates:
                    snap = build_universe_snapshot(d, daily=daily, min_list_days=10, min_adv_yi=0.0)
                    if not snap.empty:
                        write_universe_snapshot(snap)

                panel = build_panel(dates, daily=daily, adj=pd.DataFrame(), industries={})
                assert len(panel) > 0, "面板为空"
                for r in panel[:50]:
                    assert len(r.date) == 10 and r.date[4] == "-", r.date
                print(f"build_panel OK: {len(panel)} 行")

                bt_dates = [d for d in synth_dates if d >= "2024-04-01"]
                daily2 = read_daily_raw()

                def alpha_fn(d, rows):
                    return {c: float(r["close"]) for c, r in rows.items()}

                policy = TargetPortfolio(
                    n_enter=5, n_exit=10, max_stocks=5, target_vol=0.20, daily=daily2
                )
                broker = run_backtest(
                    daily=daily2, dates=bt_dates, alpha_fn=alpha_fn,
                    policy=policy, initial_cash=1_000_000, max_positions=5,
                    exit_config=ExitConfig(hard_pct=0.08, max_hold_days=15),
                    strict_signals=True,
                )
                m = compute_metrics(broker)
                assert m["n_days"] > 0
                assert len(broker.trades) > 0, "回测零成交"
                assert "sortino" in m
                print(f"backtest OK: {len(broker.trades)} 笔, equity={m['final_equity']} sharpe={m['sharpe']}")

    print("\n=== 端到端冒烟全部通过（临时目录，未污染真实库）===")


if __name__ == "__main__":
    run_smoke()
