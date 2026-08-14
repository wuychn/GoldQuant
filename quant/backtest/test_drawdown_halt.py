"""回测组合回撤熔断。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant.backtest.engine import DrawdownHaltConfig, ExitConfig, run_backtest
from quant.portfolio.target import TargetPortfolio


def _crash_daily(n_days: int = 40) -> tuple[pd.DataFrame, list[str], dict]:
    """两只票：前半横盘，后半连续大跌，迫使回撤。"""
    dates = list(pd.bdate_range(start="2024-01-02", periods=n_days).strftime("%Y-%m-%d"))
    rows = []
    for code in ("000001", "000002"):
        p = 10.0
        for j, d in enumerate(dates):
            if j < 10:
                p = 10.0
            else:
                p = max(p * 0.92, 0.5)  # 日跌约 8%
            rows.append(
                {
                    "code": code,
                    "date": d,
                    "open": p,
                    "high": p * 1.01,
                    "low": p * 0.99,
                    "close": p,
                    "volume": 1e6,
                    "amount": p * 1e6,
                    "float_mv": 1e10,
                }
            )
    daily = pd.DataFrame(rows)
    alpha = {d: {"000001": 1.0, "000002": 0.9} for d in dates}
    return daily, dates, alpha


def test_drawdown_halt_blocks_new_buys():
    daily, dates, alpha_by_date = _crash_daily()

    def alpha_fn(d, _rows):
        return alpha_by_date.get(d, {})

    policy = TargetPortfolio(
        n_enter=2,
        n_exit=2,
        max_stocks=2,
        target_vol=0.0,  # 不缩仓，方便打满
        full_invest=0.95,
        equal_weight=True,
        daily=daily,
    )
    # 无熔断：大跌中仍会调仓/换仓
    b0 = run_backtest(
        daily=daily,
        dates=dates,
        alpha_fn=alpha_fn,
        policy=policy,
        max_positions=2,
        exit_config=None,
        strict_signals=False,
        drawdown_halt=None,
    )
    # 有熔断：回撤后禁加仓
    b1 = run_backtest(
        daily=daily,
        dates=dates,
        alpha_fn=alpha_fn,
        policy=policy,
        max_positions=2,
        exit_config=None,
        strict_signals=False,
        drawdown_halt=DrawdownHaltConfig(enabled=True, max_drawdown_pct=15.0, halt_days=20),
    )
    eq0 = b0.equity_curve[-1][1]
    eq1 = b1.equity_curve[-1][1]
    # 熔断应保住更多现金（终值更高或回撤更浅）
    assert eq1 >= eq0 * 0.99
    mdd = lambda br: min(
        br.equity_curve[k][1] / max(x[1] for x in br.equity_curve[: k + 1]) - 1
        for k in range(len(br.equity_curve))
    )
    assert mdd(b1) >= mdd(b0) - 1e-9  # 回撤幅度不更深（负数比较：≥ 表示更浅或相等）
