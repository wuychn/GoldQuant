"""量能族：量比 + 换手率 z + 量价相关。"""

from __future__ import annotations

import numpy as np

from quant.factors.library.base import BarSeries, FactorDef


def vol_ratio_5_20(bars: BarSeries, as_of: str) -> float | None:
    """5 日均成交额 / 20 日均成交额。"""
    a = bars.amount_up_to(as_of)
    if len(a) < 20:
        return None
    m5 = float(a.iloc[-5:].mean())
    m20 = float(a.iloc[-20:].mean())
    if m20 <= 0:
        return None
    return m5 / m20


def turnover_z_60(bars: BarSeries, as_of: str) -> float | None:
    """换手率相对自身 60 日均值的 z。"""
    t = bars.turnover_up_to(as_of)
    if len(t) < 61:
        return None
    seg = t.iloc[-60:]
    mu = float(seg.mean())
    sd = float(seg.std(ddof=1))
    last = float(t.iloc[-1])
    if sd < 1e-12 or not np.isfinite(sd):
        return None
    return (last - mu) / sd


def vol_price_corr_20(bars: BarSeries, as_of: str) -> float | None:
    """20 日成交量与收益的相关（量价配合）。"""
    c = bars.close_up_to(as_of)
    v = bars.volume_up_to(as_of)
    if len(c) < 21 or len(v) < 21:
        return None
    rets = c.pct_change().iloc[-20:]
    vol = v.iloc[-20:]
    if len(rets) < 3 or vol.std() < 1e-12:
        return None
    return float(np.corrcoef(rets.values, vol.values)[0, 1])


VOLUME_FACTORS: list[FactorDef] = [
    FactorDef("vol_ratio_5_20", "5/20日额比", vol_ratio_5_20, direction=1.0, default_weight=0.8),
    FactorDef("turnover_z_60", "换手率z60", turnover_z_60, direction=1.0, default_weight=0.6),
    FactorDef("vol_price_corr_20", "量价相关20", vol_price_corr_20, direction=1.0, default_weight=0.6),
]
