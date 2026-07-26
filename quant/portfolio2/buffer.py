"""缓冲区：仅当目标权重与当前偏离超过阈值才调仓，降低换手。

主升波段策略换手不应过高；缓冲区让小幅漂移不触发交易。
"""

from __future__ import annotations


def apply_buffer(
    target: dict[str, float],
    current: dict[str, float],
    *,
    abs_tol: float = 0.01,  # 绝对偏离 1% 内不调
    rel_tol: float = 0.20,  # 相对偏离 20% 内不调
    drop_tol: float = 0.015,  # 目标为 0 但当前 < 1.5% 可不清
) -> dict[str, float]:
    """返回调整后的目标权重。对在缓冲区内的仓位保持当前权重。"""
    out: dict[str, float] = {}
    codes = set(target) | set(current)
    for c in codes:
        t = target.get(c, 0.0)
        cur = current.get(c, 0.0)
        if t <= 0:
            # 拟清仓
            if cur < drop_tol:
                out[c] = cur  # 太小不动，省成本
            else:
                out[c] = 0.0
            continue
        if cur <= 0:
            out[c] = t  # 新建仓
            continue
        diff = abs(t - cur)
        if diff <= abs_tol or diff / max(cur, 1e-6) <= rel_tol:
            out[c] = cur  # 缓冲区内不调
        else:
            out[c] = t
    return out
