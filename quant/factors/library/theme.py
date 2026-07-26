"""主题族：所属行业板块动量的横截面分位。

优先用 ``bars.extras['theme_mom']``；否则用同日同行业截面动量代理
（由 panel_builder 在批量构建时写入 extras）。
"""

from __future__ import annotations

from quant.factors.library.base import BarSeries, FactorDef


def theme_mom(bars: BarSeries, as_of: str) -> float | None:
    extras = getattr(bars, "extras", None) or {}
    v = extras.get("theme_mom")
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if abs(f) < 1e8 else None


THEME_FACTORS: list[FactorDef] = [
    FactorDef("theme_mom", "主题/行业动量分位", theme_mom, direction=1.0, default_weight=0.6),
]
