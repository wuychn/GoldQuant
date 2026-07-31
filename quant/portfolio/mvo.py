"""均值-方差组合优化：max μ'w - (λ/2) w'Σw，联立单票/行业/概念/风格 cap（SLSQP）。"""

from __future__ import annotations

import numpy as np

from quant.portfolio.constraints import apply_single_cap


def _group_caps(
    codes: list[str],
    groups: dict[str, str],
    cap: float,
) -> list[tuple[np.ndarray, float]]:
    """分组暴露上限：Σ w_i ≤ cap。"""
    if cap <= 0 or not groups:
        return []
    by_g: dict[str, list[int]] = {}
    for i, c in enumerate(codes):
        g = groups.get(c)
        if g:
            by_g.setdefault(g, []).append(i)
    out: list[tuple[np.ndarray, float]] = []
    for idxs in by_g.values():
        if len(idxs) < 2:
            continue
        row = np.zeros(len(codes), dtype=float)
        for j in idxs:
            row[j] = 1.0
        out.append((row, cap))
    return out


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
    sectors: dict[str, str] | None = None,
    sector_cap: float = 0.0,
    concepts: dict[str, list[str]] | None = None,
    concept_cap: float = 0.0,
    style_buckets: dict[str, str] | None = None,
    style_caps: dict[str, float] | None = None,
    style_groups: list[tuple[dict[str, str], float]] | None = None,
) -> dict[str, float]:
    """SLSQP 约束 MVO；无 scipy 时回退解析解 + 投影。"""
    if not codes:
        return {}
    n = len(codes)
    mu = np.array([float(alpha.get(c, 0.0)) for c in codes], dtype=float)
    if cov is not None:
        C = np.array(
            [
                [
                    float(
                        cov.get(a, {}).get(
                            b, sigmas.get(a, 0.2) * sigmas.get(b, 0.2) * (corr if a != b else 1.0)
                        )
                    )
                    for b in codes
                ]
                for a in codes
            ],
            dtype=float,
        )
    else:
        sig = np.array([sigmas.get(c, 0.2) for c in codes], dtype=float)
        C = np.outer(sig, sig) * corr
        np.fill_diagonal(C, sig**2)
    C += np.eye(n) * 1e-8
    lam = max(risk_aversion, 1e-6)

    bounds = [(0.0, max_weight if max_weight > 0 else 1.0) for _ in codes]
    ineq: list[tuple[np.ndarray, float]] = []
    if sectors and sector_cap > 0:
        ineq.extend(_group_caps(codes, sectors, sector_cap))
    if concepts and concept_cap > 0:
        concept_names = {n for ns in concepts.values() for n in (ns or []) if n}
        for name in concept_names:
            g = {c: name for c in codes if name in (concepts.get(c) or [])}
            ineq.extend(_group_caps(codes, g, concept_cap))
    if style_groups:
        for groups, cap in style_groups:
            if cap > 0 and groups:
                ineq.extend(_group_caps(codes, groups, cap))
    elif style_buckets and style_caps:
        for bucket, cap in style_caps.items():
            if cap <= 0:
                continue
            g = {c: b for c, b in style_buckets.items() if b == bucket}
            ineq.extend(_group_caps(codes, g, cap))

    w0 = np.full(n, full_invest / n)
    try:
        from scipy.optimize import minimize

        def obj(w: np.ndarray) -> float:
            return -float(mu @ w) + 0.5 * lam * float(w @ C @ w)

        cons = [{"type": "eq", "fun": lambda w, fi=full_invest: np.sum(w) - fi}]
        for row, cap in ineq:
            cons.append({"type": "ineq", "fun": lambda w, r=row, c=cap: c - float(r @ w)})

        res = minimize(obj, w0, method="SLSQP", bounds=bounds, constraints=cons, options={"maxiter": 200, "ftol": 1e-9})
        if res.success and np.all(np.isfinite(res.x)):
            w_arr = np.maximum(res.x, 0.0)
            s = w_arr.sum()
            if s > 1e-12:
                return {c: float(w_arr[i]) for i, c in enumerate(codes)}
    except ImportError:
        pass

    # 回退：解析解 + 单票 cap 迭代
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
