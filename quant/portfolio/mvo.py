"""均值-方差组合优化：max μ'w - (λ/2) w'Σw，带单票上限投影。"""

from __future__ import annotations

import numpy as np

from quant.portfolio.constraints import apply_single_cap


def optimize_mvo(
    codes: list[str],
    alpha: dict[str, float],
    cov: dict[str, dict[str, float]] | None,
    sigmas: dict[str, float],
    *,
    risk_aversion: float = 2.0,
    max_weight: float = 0.25,
    full_invest: float = 0.95,
    corr: float = 0.3,
) -> dict[str, float]:
    """解析 MVO + 非负约束 + 单票 cap（迭代投影）。"""
    if not codes:
        return {}
    n = len(codes)
    mu = np.array([float(alpha.get(c, 0.0)) for c in codes], dtype=float)
    if cov is not None:
        C = np.array(
            [[float(cov.get(a, {}).get(b, sigmas.get(a, 0.2) * sigmas.get(b, 0.2) * (corr if a != b else 1.0)))
              for b in codes]
             for a in codes],
            dtype=float,
        )
    else:
        sig = np.array([sigmas.get(c, 0.2) for c in codes], dtype=float)
        C = np.outer(sig, sig) * corr
        np.fill_diagonal(C, sig ** 2)
    C += np.eye(n) * 1e-8
    lam = max(risk_aversion, 1e-6)
    try:
        w_raw = np.linalg.solve(C, mu / lam)
    except np.linalg.LinAlgError:
        w_raw = mu.copy()
    w_raw = np.maximum(w_raw, 0.0)
    if w_raw.sum() <= 1e-12:
        base = full_invest / n
        return {c: base for c in codes}
    w = {c: float(w_raw[i] / w_raw.sum() * full_invest) for i, c in enumerate(codes)}
    for _ in range(5):
        w = apply_single_cap(w, max_weight)
        s = sum(w.values())
        if s > 0 and full_invest > 0:
            w = {c: v * (full_invest / s) for c, v in w.items()}
    return w
