"""主题族：所属概念/行业板块动量的横截面分位。

板块动量需板块日线；离线库暂未落板块行情，回测置空，实盘可由 payload 注入。
"""

from __future__ import annotations

from quant.factors.library.base import BarSeries, FactorDef


def theme_mom(bars: BarSeries, as_of: str) -> float | None:
    """所属概念板块 20 日动量的横截面分位。

    需板块日线与个股→板块映射；离线库暂无，回测返回 None。
    实盘链路可由 payload 的板块行情注入后覆盖。
    """
    return None


THEME_FACTORS: list[FactorDef] = [
    FactorDef("theme_mom", "主题板块动量分位", theme_mom, direction=1.0, default_weight=0.6),
]
