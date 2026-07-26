"""约束：单票上限 + 行业上限 + 持仓数上限。"""

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


def truncate_to_n(weights: dict[str, float], n: int) -> dict[str, float]:
    """保留权重最大的 n 只，其余置 0。"""
    if n <= 0 or len(weights) <= n:
        return dict(weights)
    ranked = sorted(weights.items(), key=lambda kv: -kv[1])[:n]
    return dict(ranked)
