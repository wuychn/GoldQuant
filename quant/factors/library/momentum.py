"""动量族：短期反转 + 中期动量 + 加速度。

A股 1 个月内是反转、3-12 月是动量，故中期动量窗口避开最近 20 日。
"""

from __future__ import annotations

from quant.factors.library.base import BarSeries, FactorDef, _ret


def mom_20(bars: BarSeries, as_of: str) -> float | None:
    """近 20 日收益（短期反转，方向取负）。"""
    return _ret(bars.close_up_to(as_of), 20)


def mom_60(bars: BarSeries, as_of: str) -> float | None:
    """近 60 日收益（中期动量）。"""
    return _ret(bars.close_up_to(as_of), 60)


def mom_120_20(bars: BarSeries, as_of: str) -> float | None:
    """过去 [20,120] 日收益（剔除最近 20 日的动量）。"""
    c = bars.close_up_to(as_of)
    if len(c) < 121:
        return None
    a = float(c.iloc[-121])
    b = float(c.iloc[-21])
    if a <= 0:
        return None
    return b / a - 1.0


MOMENTUM_FACTORS: list[FactorDef] = [
    FactorDef("mom_20", "20日收益(反转)", mom_20, direction=-1.0, default_weight=0.8),
    FactorDef("mom_60", "60日收益(动量)", mom_60, direction=1.0, default_weight=1.0),
    FactorDef("mom_120_20", "[20,120]日动量", mom_120_20, direction=1.0, default_weight=1.0),
]
