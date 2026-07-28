"""缓冲区：排名区（N_enter / N_exit）+ 权重偏离缓冲。

计划口径：
- rank <= N_enter 才买入
- 已持仓 rank <= N_exit 才保留（N_exit > N_enter）
- 中间排名波动不触发交易
另保留绝对/相对权重缓冲，进一步压小幅漂移换手。
"""

from __future__ import annotations


def apply_rank_buffer(
    alpha: dict[str, float],
    current: dict[str, float],
    *,
    n_enter: int,
    n_exit: int,
) -> list[str]:
    """返回应进入目标组合的代码列表（含新建仓与保留仓）。"""
    if n_enter <= 0:
        return []
    n_exit = max(n_exit, n_enter)
    ranked = sorted(alpha.items(), key=lambda kv: -kv[1])
    rank_of = {c: i + 1 for i, (c, _) in enumerate(ranked)}

    keep: list[str] = []
    # 新建：rank <= N_enter
    for c, _ in ranked[:n_enter]:
        keep.append(c)
    # 已持仓：rank <= N_exit 且不在 keep 中
    held = [c for c, w in current.items() if w > 1e-9]
    for c in held:
        r = rank_of.get(c)
        if r is not None and r <= n_exit and c not in keep:
            keep.append(c)
    return keep


def apply_buffer(
    target: dict[str, float],
    current: dict[str, float],
    *,
    abs_tol: float = 0.01,
    rel_tol: float = 0.20,
    drop_tol: float = 0.015,
) -> dict[str, float]:
    """权重偏离缓冲：缓冲区内保持当前权重。"""
    out: dict[str, float] = {}
    codes = set(target) | set(current)
    for c in codes:
        t = target.get(c, 0.0)
        cur = current.get(c, 0.0)
        if t <= 0:
            if cur < drop_tol:
                out[c] = cur
            else:
                out[c] = 0.0
            continue
        if cur <= 0:
            out[c] = t
            continue
        diff = abs(t - cur)
        if diff <= abs_tol or diff / max(cur, 1e-6) <= rel_tol:
            out[c] = cur
        else:
            out[c] = t
    return out
