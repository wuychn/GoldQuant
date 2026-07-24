"""中性化因子合成 alpha，并映射到 0–100 评分。"""

from __future__ import annotations

from quant.factors.base import FactorRow
from quant.factors.raw import default_weights


def compose_row_alpha(
    row: FactorRow,
    *,
    use_neutral: bool = True,
    weights: dict[str, float] | None = None,
) -> float | None:
    """单行合成 alpha（加权 z 均值）。"""
    wmap = weights or default_weights()
    src = row.neutral if use_neutral and row.neutral else row.raw
    if not src:
        return None
    num = 0.0
    den = 0.0
    for k, v in src.items():
        w = float(wmap.get(k, 1.0))
        if w <= 0:
            continue
        num += float(v) * w
        den += w
    if den <= 0:
        return None
    return num / den


def compose_neutral_alpha(
    rows: list[FactorRow],
    *,
    use_neutral: bool = True,
    weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """返回 {code: alpha}。"""
    out: dict[str, float] = {}
    for r in rows:
        a = compose_row_alpha(r, use_neutral=use_neutral, weights=weights)
        if a is not None:
            out[r.code] = a
    return out


def map_alpha_to_score(alpha: float, *, loc: float = 50.0, scale: float = 12.0) -> float:
    """将截面 alpha(z) 映射到约 0–100；中性≈50。"""
    s = loc + alpha * scale
    return max(0.0, min(100.0, s))


def alpha_scores_0_100(
    rows: list[FactorRow],
    *,
    use_neutral: bool = True,
) -> dict[str, float]:
    alphas = compose_neutral_alpha(rows, use_neutral=use_neutral)
    return {c: map_alpha_to_score(a) for c, a in alphas.items()}
