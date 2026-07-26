"""热度族：人气榜排名 z。仅实盘有当日榜时可算；回测无历史榜置空。"""

from __future__ import annotations

from quant.factors.library.base import BarSeries, FactorDef


def hot_rank_z(bars: BarSeries, as_of: str) -> float | None:
    """人气榜排名的横截面 z（越大越热）。

    回测默认 None。实盘由 panel_builder 通过 ``bars.extras['hot_rank_z']`` 注入，
    或由日频决策脚本在合成 alpha 前合并。
    """
    extras = getattr(bars, "extras", None) or {}
    v = extras.get("hot_rank_z")
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if abs(f) < 1e8 else None


HOT_FACTORS: list[FactorDef] = [
    FactorDef("hot_rank_z", "人气榜排名z(仅实盘)", hot_rank_z, direction=1.0, default_weight=0.4),
]
