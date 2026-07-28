"""热度族：人气榜排名 z。

优先级：``bars.extras['hot_rank_z']`` → PIT 快照（panel_builder 注入）。
无历史快照时返回 None（回测不引入前视）；实盘由 update_daily 落库快照。
"""

from __future__ import annotations

from quant.factors.library.base import BarSeries, FactorDef


def hot_rank_z(bars: BarSeries, as_of: str) -> float | None:
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
    FactorDef("hot_rank_z", "人气榜排名z(PIT快照)", hot_rank_z, direction=1.0, default_weight=0.4),
]
