"""参数敏感性：网格扫描 + 稳定性评分。

主升波段关键参数：atr_mult / hard_pct / max_hold_days / target_vol / n。
对每个参数做单变量扫描，看 Sharpe 是否平稳（不出现尖峰）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import numpy as np


@dataclass
class SensitivityResult:
    param: str
    grid: list[float]
    sharpes: list[float]
    stability: float  # 1 - std/|mean|，越接近 1 越稳
    peak_to_median: float  # 峰值/中位，越大越像过拟合


def _stability(sharpes: list[float]) -> tuple[float, float]:
    arr = np.array([s for s in sharpes if np.isfinite(s)], dtype=float)
    if len(arr) < 2:
        return 0.0, 0.0
    mu = float(np.median(arr))
    sd = float(arr.std(ddof=1))
    stab = max(0.0, 1.0 - sd / max(abs(mu), 1e-6))
    peak = float(arr.max())
    ptm = peak / max(abs(mu), 1e-6)
    return stab, ptm


def scan_param(
    param: str,
    grid: list[float],
    *,
    run_fn: Callable[[float], float],  # param_value → Sharpe
) -> SensitivityResult:
    sharpes = []
    for v in grid:
        try:
            sharpes.append(float(run_fn(v)))
        except Exception:
            sharpes.append(float("nan"))
    stab, ptm = _stability(sharpes)
    return SensitivityResult(param=param, grid=grid, sharpes=sharpes, stability=round(stab, 3), peak_to_median=round(ptm, 3))


def parameter_budget(n_params: int, n_values_per_param: int) -> int:
    """参数预算：总试验数 = 取值数^参数数。用于 DSR 的 n_trials。"""
    return n_values_per_param ** n_params
