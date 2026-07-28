"""绩效统计辅助：Newey-West 修正 Sharpe 等。"""

from __future__ import annotations

import numpy as np


def newey_west_sharpe(
    rets: np.ndarray,
    *,
    trading_days: int = 252,
    lags: int | None = None,
) -> float:
    """对日收益序列算 Newey-West 修正年化 Sharpe（均值/ NW 标准误 × sqrt(252)）。"""
    r = np.asarray(rets, dtype=float)
    r = r[np.isfinite(r)]
    n = len(r)
    if n < 3:
        return 0.0
    mu = float(r.mean())
    if lags is None:
        lags = max(1, int(4 * (n / 100) ** (2 / 9)))
    lags = min(lags, n - 1)
    gamma0 = float(np.dot(r - mu, r - mu) / n)
    var_nw = gamma0
    for lag in range(1, lags + 1):
        w = 1.0 - lag / (lags + 1)
        cov = float(np.dot(r[lag:] - mu, r[:-lag] - mu) / n)
        var_nw += 2 * w * cov
    if var_nw <= 1e-18:
        return 0.0
    ann_ret = mu * trading_days
    ann_vol_nw = float(np.sqrt(var_nw * trading_days))
    return ann_ret / ann_vol_nw if ann_vol_nw > 1e-12 else 0.0
