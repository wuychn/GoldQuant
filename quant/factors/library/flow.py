"""资金族：主力净流入/流通市值。

离线库无资金流历史时返回 None；可由 ``bars.extras['flow_ratio_5']`` 注入。
"""

from __future__ import annotations

from quant.factors.library.base import BarSeries, FactorDef


def flow_ratio_5(bars: BarSeries, as_of: str) -> float | None:
    extras = getattr(bars, "extras", None) or {}
    v = extras.get("flow_ratio_5")
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if abs(f) < 1e8 else None


FLOW_FACTORS: list[FactorDef] = [
    FactorDef("flow_ratio_5", "5日主力净流入/市值", flow_ratio_5, direction=1.0, default_weight=0.6),
]
