"""回测参数敏感性报告。"""

from __future__ import annotations

from typing import Any, Callable

from quant.research.sensitivity import scan_param


def run_backtest_sensitivity(
    *,
    base_run_fn: Callable[[dict[str, float]], float],
    grids: dict[str, list[float]] | None = None,
) -> dict[str, Any]:
    """对关键参数做单变量 Sharpe 扫描。"""
    grids = grids or {
        "n_enter": [6.0, 8.0, 10.0, 12.0],
        "target_vol": [0.12, 0.15, 0.18],
        "buffer_abs": [0.005, 0.01, 0.02],
        "sector_cap": [0.30, 0.40, 0.50],
    }
    out: dict[str, Any] = {}
    for param, grid in grids.items():
        res = scan_param(param, grid, run_fn=lambda v, p=param: base_run_fn({p: v}))
        out[param] = {
            "grid": res.grid,
            "sharpes": res.sharpes,
            "stability": res.stability,
            "peak_to_median": res.peak_to_median,
        }
    return out
