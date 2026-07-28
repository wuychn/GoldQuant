"""因子合成：中性化 z-score 加权 → alpha（r3 截面面板）。

compose_alpha / rank_alpha / top_n / alpha_attribution 面板合成；
compose_intraday_alpha 盘中择时合成。
"""

from __future__ import annotations

import numpy as np

from quant.factors.base import FactorRow
from quant.factors.registry import FactorRegistry, REGISTRY


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
    # 固定正权重因子集 → 分母跨票一致，避免缺因子票 den 偏小导致 |alpha| 放大
    active = [(k, float(w)) for k, w in wmap.items() if float(w) > 0]
    den = sum(w for _, w in active)
    if den <= 0:
        return {}
    out: dict[str, float] = {}
    for r in rows:
        src = r.neutral if (use_neutral and r.neutral) else r.raw
        num = 0.0
        for k, w in active:
            v = src.get(k)
            if v is None or not np.isfinite(v):
                continue  # 缺失因子按 z 中位数 0 贡献（不累加 num）
            num += float(v) * w
        out[r.code] = num / den
    return out


def rank_alpha(alpha: dict[str, float]) -> list[str]:
    """alpha 降序排名，返回代码列表（头部最强）。"""
    return sorted(alpha.keys(), key=lambda c: -alpha[c])


def alpha_attribution(
    rows: list[FactorRow],
    *,
    registry: FactorRegistry = REGISTRY,
    weights: dict[str, float] | None = None,
    use_neutral: bool = True,
    top_k: int = 3,
) -> dict[str, list[tuple[str, float]]]:
    """返回 {code: [(factor, contribution), ...]}，按 |贡献| 降序取 top_k。

    contribution = z × weight / den（与 ``compose_alpha`` 同口径，含 direction），
    供叙事文案生成"动量强/资金流入"等归因短语（见 ``narrative/factor_phrases``）。
    """
    wmap = weights or registry.weights()
    active = [(k, float(w)) for k, w in wmap.items() if float(w) > 0]
    den = sum(w for _, w in active)
    if den <= 0:
        return {}
    out: dict[str, list[tuple[str, float]]] = {}
    for r in rows:
        src = r.neutral if (use_neutral and r.neutral) else r.raw
        contribs: list[tuple[str, float]] = []
        for k, w in active:
            v = src.get(k)
            if v is None or not np.isfinite(v):
                continue
            contribs.append((k, float(v) * w / den))
        contribs.sort(key=lambda kv: -abs(kv[1]))
        out[r.code] = contribs[:top_k]
    return out


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
