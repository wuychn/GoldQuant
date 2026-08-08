"""参数敏感性：网格扫描 + 稳定性评分。

主升波段关键参数：atr_mult / hard_pct / max_hold_days / target_vol / n。
对每个参数做单变量扫描，看 Sharpe 是否平稳（不出现尖峰）。
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import Callable

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


def _safe_run_fn(run_fn: Callable[[float], float], v: float) -> float:
    """模块级包装，供 ProcessPool 调用（run_fn 须可 pickle）。"""
    try:
        return float(run_fn(v))
    except Exception:
        return float("nan")


def scan_param(
    param: str,
    grid: list[float],
    *,
    run_fn: Callable[[float], float],  # param_value → Sharpe
    workers: int = 1,
) -> SensitivityResult:
    """单变量扫描。``workers>1`` 时要求 ``run_fn`` 可被 pickle（模块级函数）。"""
    workers = max(1, int(workers))
    if workers <= 1 or len(grid) <= 1:
        sharpes = []
        for v in grid:
            sharpes.append(_safe_run_fn(run_fn, float(v)))
    else:
        with ProcessPoolExecutor(max_workers=min(workers, len(grid))) as pool:
            sharpes = list(
                pool.map(_safe_run_fn, [run_fn] * len(grid), [float(v) for v in grid])
            )
    stab, ptm = _stability(sharpes)
    return SensitivityResult(
        param=param,
        grid=list(grid),
        sharpes=sharpes,
        stability=round(stab, 3),
        peak_to_median=round(ptm, 3),
    )


def parameter_budget(n_params: int, n_values_per_param: int) -> int:
    """参数预算：总试验数 = 取值数^参数数。用于 DSR 的 n_trials。"""
    return n_values_per_param ** n_params
