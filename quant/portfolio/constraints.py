"""约束：单票上限 + 行业上限 + 多概念上限 + 持仓数上限。"""

from __future__ import annotations


def apply_single_cap(weights: dict[str, float], cap: float) -> dict[str, float]:
    """单票权重封顶，超出部分按比例分摊给未封顶票。"""
    if cap <= 0:
        return dict(weights)
    w = dict(weights)
    for _ in range(3):
        over = {c: v for c, v in w.items() if v > cap}
        if not over:
            break
        excess = sum(v - cap for v in over.values())
        for c in over:
            w[c] = cap
        under = {c: v for c, v in w.items() if v < cap}
        if under:
            u_total = sum(under.values())
            for c, v in under.items():
                w[c] = v + excess * (v / u_total if u_total > 0 else 1 / len(under))
    return w


def apply_sector_cap(weights: dict[str, float], sectors: dict[str, str], cap: float) -> dict[str, float]:
    """行业权重封顶：超出行业的票按比例缩放。"""
    if cap <= 0 or not sectors:
        return dict(weights)
    by_sec: dict[str, float] = {}
    for c, w in weights.items():
        s = sectors.get(c, "其他")
        by_sec[s] = by_sec.get(s, 0.0) + w
    over_sec = {s: w for s, w in by_sec.items() if w > cap}
    if not over_sec:
        return dict(weights)
    out = dict(weights)
    for s, sw in over_sec.items():
        k = cap / sw
        for c, w in out.items():
            if sectors.get(c, "其他") == s:
                out[c] = w * k
    return out


def apply_concept_cap(
    weights: dict[str, float],
    concepts: dict[str, list[str]],
    cap: float,
) -> dict[str, float]:
    """多概念暴露封顶：每只票的全部概念分别累加，任一概念超限则缩放该概念下的票。

    ``concepts``: {code: [概念1, 概念2, ...]}。取首概念会低估集中度，故必须用全量列表。
    """
    if cap <= 0 or not concepts:
        return dict(weights)
    # 概念 → 暴露
    by_c: dict[str, float] = {}
    for code, w in weights.items():
        for name in concepts.get(code) or []:
            if not name:
                continue
            by_c[name] = by_c.get(name, 0.0) + w
    over = {n: e for n, e in by_c.items() if e > cap}
    if not over:
        return dict(weights)
    out = dict(weights)
    # 迭代缩放直到全部概念不超过 cap（最多 5 轮）
    for _ in range(5):
        by_c = {}
        for code, w in out.items():
            for name in concepts.get(code) or []:
                if name:
                    by_c[name] = by_c.get(name, 0.0) + w
        over = {n: e for n, e in by_c.items() if e > cap + 1e-9}
        if not over:
            break
        for name, exp in over.items():
            k = cap / exp
            for code in list(out):
                if name in (concepts.get(code) or []):
                    out[code] = out[code] * k
    return out


def truncate_to_n(weights: dict[str, float], n: int) -> dict[str, float]:
    """保留权重最大的 n 只，其余置 0。"""
    if n <= 0 or len(weights) <= n:
        return dict(weights)
    ranked = sorted(weights.items(), key=lambda kv: -kv[1])[:n]
    return dict(ranked)


def apply_style_cap(
    weights: dict[str, float],
    style_buckets: dict[str, str],
    caps: dict[str, float],
) -> dict[str, float]:
    """风格暴露封顶：``style_buckets`` {code: bucket_name}，``caps`` {bucket: max_weight}。"""
    if not style_buckets or not caps:
        return dict(weights)
    out = dict(weights)
    for _ in range(5):
        by_b: dict[str, float] = {}
        for c, w in out.items():
            b = style_buckets.get(c)
            if b:
                by_b[b] = by_b.get(b, 0.0) + w
        over = {b: e for b, e in by_b.items() if e > caps.get(b, 1.0) + 1e-9}
        if not over:
            break
        for b, exp in over.items():
            cap = caps.get(b, 1.0)
            k = cap / exp
            for c in list(out):
                if style_buckets.get(c) == b:
                    out[c] = out[c] * k
    return out


def alpha_strength_weights(
    alpha: dict[str, float],
    codes: list[str],
    *,
    full_invest: float,
    shrink: float = 0.5,
) -> dict[str, float]:
    """alpha 强度配权：softmax(z) 与等权混合（shrink→1 趋等权）。"""
    if not codes:
        return {}
    vals = [float(alpha.get(c, 0.0)) for c in codes]
    mu = sum(vals) / len(vals)
    sd = (sum((v - mu) ** 2 for v in vals) / max(len(vals), 1)) ** 0.5
    if sd < 1e-9:
        base = full_invest / len(codes)
        return {c: base for c in codes}
    z = [(v - mu) / sd for v in vals]
    import math

    exp_z = [math.exp(min(3.0, max(-3.0, x))) for x in z]
    s = sum(exp_z) or 1.0
    soft = {c: full_invest * e / s for c, e in zip(codes, exp_z)}
    eq = full_invest / len(codes)
    shrink = max(0.0, min(1.0, shrink))
    return {c: shrink * eq + (1.0 - shrink) * soft[c] for c in codes}

