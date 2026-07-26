"""位置族：距 252 日高点距离 + MA 发散 + MA 斜率。

dist_high_252 是 52 周新高效应，主升浪最直接的因子化表达，取代形态规则。
"""

from __future__ import annotations

import numpy as np

from quant.factors.library.base import BarSeries, FactorDef


def dist_high_252(bars: BarSeries, as_of: str) -> float | None:
    """距 252 日最高价的距离（负值，越接近 0 越强）。"""
    h = bars.high_up_to(as_of)
    c = bars.close_up_to(as_of)
    if len(h) < 10:
        return None
    window = h.iloc[-min(len(h), 252):]
    hi = float(window.max())
    last = float(c.iloc[-1])
    if hi <= 0:
        return None
    return last / hi - 1.0


def ma_spread(bars: BarSeries, as_of: str) -> float | None:
    """MA5 / MA20 - 1，发散度。"""
    c = bars.close_up_to(as_of)
    if len(c) < 20:
        return None
    ma5 = float(c.iloc[-5:].mean())
    ma20 = float(c.iloc[-20:].mean())
    if ma20 <= 0:
        return None
    return ma5 / ma20 - 1.0


def ma_slope_20(bars: BarSeries, as_of: str) -> float | None:
    """MA20 的 20 日斜率 / 价格。"""
    c = bars.close_up_to(as_of)
    if len(c) < 40:
        return None
    ma20_now = float(c.iloc[-20:].mean())
    ma20_prev = float(c.iloc[-40:-20].mean())
    price = float(c.iloc[-1])
    if price <= 0 or not np.isfinite(ma20_prev):
        return None
    return (ma20_now - ma20_prev) / price


POSITION_FACTORS: list[FactorDef] = [
    FactorDef("dist_high_252", "距252日高点", dist_high_252, direction=1.0, default_weight=1.0),
    FactorDef("ma_spread", "MA5/MA20发散", ma_spread, direction=1.0, default_weight=0.8),
    FactorDef("ma_slope_20", "MA20斜率", ma_slope_20, direction=1.0, default_weight=0.6),
]
