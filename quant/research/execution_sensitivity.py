"""执行敏感性分析。"""

from __future__ import annotations

from typing import Any

from quant.backtest.broker import BrokerConfig
from quant.backtest.engine import run_backtest


def execution_sensitivity_grid(
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    slip_bps_list: list[float] | None = None,
    mode: str = "intraday_replay",
) -> dict[str, Any]:
    """滑点网格下的 PnL 曲面。"""
    slip_bps_list = slip_bps_list or [0, 5, 10, 20]
    results = []
    for bps in slip_bps_list:
        import os

        os.environ["GOLDQUANT_BACKTEST_SLIP"] = str(bps / 10000)
        m = run_backtest(
            from_date=from_date,
            to_date=to_date,
            broker_cfg=BrokerConfig(),
            mode=mode,
        )
        results.append(
            {
                "slippage_bps": bps,
                "total_return": m.get("total_return"),
                "sharpe_ratio": m.get("sharpe_ratio"),
                "max_drawdown": m.get("max_drawdown"),
            }
        )
    break_even = None
    for r in results:
        if float(r.get("total_return") or 0) <= 0:
            break_even = r["slippage_bps"]
            break
    return {"grid": results, "break_even_slip_bps": break_even}
