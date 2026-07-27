"""因子合成：中性化 z-score 加权 → alpha。

旧接口（compose_row_alpha / compose_neutral_alpha / alpha_scores_0_100）保留，
供旧评分链路使用；新接口（compose_alpha / rank_alpha / top_n）面向 r3 面板。
"""

from __future__ import annotations

import numpy as np

from quant.factors.base import FactorRow
from quant.factors.registry import FactorRegistry, REGISTRY


def _default_weights() -> dict[str, float]:
    """惰性导入旧 raw.default_weights（其依赖 yaml/akshare 重链）。"""
    from quant.factors.raw import default_weights

    return default_weights()


# ---------- 旧接口（保留） ----------


def compose_row_alpha(
    row: FactorRow,
    *,
    use_neutral: bool = True,
    weights: dict[str, float] | None = None,
) -> float | None:
    """单行合成 alpha（加权 z 均值）。"""
    wmap = weights or _default_weights()
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


# ---------- 新接口（r3 面板） ----------


def compose_alpha(
    rows: list[FactorRow],
    *,
    registry: FactorRegistry = REGISTRY,
    weights: dict[str, float] | None = None,
    use_neutral: bool = True,
) -> dict[str, float]:
    """合成截面 alpha：加权 z-score 均值。返回 {code: alpha}。

    每个 row 用 row.neutral（中性化 z）或回退 row.raw；权重取 registry 默认或覆盖。
    """
    wmap = weights or registry.weights()
    out: dict[str, float] = {}
    for r in rows:
        src = r.neutral if (use_neutral and r.neutral) else r.raw
        if not src:
            continue
        num = 0.0
        den = 0.0
        for k, v in src.items():
            w = float(wmap.get(k, 1.0))
            if w <= 0 or v is None or not np.isfinite(v):
                continue
            num += float(v) * w
            den += w
        if den > 0:
            out[r.code] = num / den
    return out


def rank_alpha(alpha: dict[str, float]) -> list[str]:
    """alpha 降序排名，返回代码列表（头部最强）。"""
    return sorted(alpha.keys(), key=lambda c: -alpha[c])


def top_n(alpha: dict[str, float], n: int) -> list[str]:
    return rank_alpha(alpha)[:n]


# ---------- 盘中因子合成（r3 择时层） ----------


def compose_intraday_alpha(
    rows: list,
    *,
    weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """盘中因子截面 z-score 加权合成 intraday_alpha。返回 ``{code: alpha_z}``。

    与日频 ``compose_alpha`` 平行，但数据源是 ``SpotRow``（实时快照，见
    ``quant.factors.library.intraday``），z-score 在本函数内做（不做行业/市值
    中性化——那是日频选股层的事；择时层只看池内相对强弱）。
    """
    from quant.factors.library.intraday import INTRADAY_FACTORS

    wmap = weights or {f.name: f.default_weight for f in INTRADAY_FACTORS}
    # 1. 各因子原始值（乘方向）
    raw: dict[str, dict[str, float]] = {f.name: {} for f in INTRADAY_FACTORS}
    for r in rows:
        for f in INTRADAY_FACTORS:
            v = f.compute(r)
            if v is not None and np.isfinite(v):
                raw[f.name][r.code] = f.direction * float(v)
    # 2. 截面 z-score
    z: dict[str, dict[str, float]] = {}
    for fname, vals in raw.items():
        arr = list(vals.values())
        if not arr:
            z[fname] = {}
            continue
        mean = float(np.mean(arr))
        std = float(np.std(arr)) if len(arr) > 1 else 0.0
        if std < 1e-9:
            std = 1.0
        z[fname] = {c: (v - mean) / std for c, v in vals.items()}
    # 3. 加权合成
    out: dict[str, float] = {}
    for r in rows:
        num = 0.0
        den = 0.0
        for fname, w in wmap.items():
            if w <= 0:
                continue
            zv = z.get(fname, {}).get(r.code)
            if zv is None:
                continue
            num += zv * w
            den += w
        if den > 0:
            out[r.code] = num / den
    return out
