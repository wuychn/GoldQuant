"""波动率目标：权重 ∝ 1/实现波动率，再缩放到目标组合波动率。

替代离散 regime 切换：用波动率倒数加权天然给高波动票小仓位，
再用 scalar 把组合年化波动率拉到 target_vol（如 15%）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def realized_vol(daily: pd.DataFrame, code: str, as_of: str, lookback: int = 20, ann: int = 252) -> float | None:
    sub = daily[(daily["code"] == code) & (daily["date"] <= as_of)].sort_values("date")
    if len(sub) < lookback + 1:
        return None
    closes = pd.to_numeric(sub["close"], errors="coerce").iloc[-(lookback + 1):]
    rets = closes.pct_change().dropna()
    if len(rets) < 2:
        return None
    return float(rets.std(ddof=1) * np.sqrt(ann))


def inv_vol_weights(vols: dict[str, float], *, max_weight: float = 0.20, min_weight: float = 0.0) -> dict[str, float]:
    """1/波动率倒数加权，单票封顶 max_weight，归一化。"""
    inv = {c: 1.0 / max(v, 1e-6) for c, v in vols.items() if v and v > 0}
    if not inv:
        return {}
    total = sum(inv.values())
    w = {c: v / total for c, v in inv.items()}
    # 封顶再归一化（迭代 3 次）
    for _ in range(3):
        over = {c: wt for c, wt in w.items() if wt > max_weight}
        if not over:
            break
        excess = sum(wt - max_weight for wt in over.values())
        for c in over:
            w[c] = max_weight
        under = [c for c in w if w[c] < max_weight]
        if under:
            for c in under:
                w[c] += excess / len(under)
    return {c: max(min_weight, wt) for c, wt in w.items()}


def scale_to_target_vol(weights: dict[str, float], vols: dict[str, float], target_vol: float, *, corr: float = 0.3) -> dict[str, float]:
    """把组合年化波动率缩放到 target_vol。

    简化：用平均相关系数 rho 估组合方差
      σ_p² = Σ w_i² σ_i² + Σ_{i≠j} w_i w_j ρ σ_i σ_j
    """
    if not weights:
        return weights
    codes = list(weights.keys())
    w = np.array([weights[c] for c in codes], dtype=float)
    sig = np.array([vols.get(c, 0.2) for c in codes], dtype=float)
    var = float((w ** 2 * sig ** 2).sum())
    pair = float((w[:, None] * w[None, :] * np.outer(sig, sig)).sum())
    diag = float((w ** 2 * sig ** 2).sum())
    cross = (pair - diag) * corr
    port_vol = np.sqrt(max(var + cross, 1e-12))
    if port_vol < 1e-9:
        return weights
    k = target_vol / port_vol
    return {c: float(weights[c] * k) for c in codes}
