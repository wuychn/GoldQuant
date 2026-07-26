"""质量族：效率比 + 波动率 + 下行波动。

eff_ratio（Kaufman 效率比）= |净涨幅| / Σ|日收益|，衡量趋势顺滑度。
"""

from __future__ import annotations

import numpy as np

from quant.factors.library.base import BarSeries, FactorDef, _std_ret


def eff_ratio_60(bars: BarSeries, as_of: str) -> float | None:
    """60 日效率比：|净涨幅| / Σ|日收益|，越接近 1 越顺滑。"""
    c = bars.close_up_to(as_of)
    if len(c) < 61:
        return None
    seg = c.iloc[-61:]
    net = abs(float(seg.iloc[-1]) - float(seg.iloc[0]))
    path = float(seg.diff().abs().sum())
    if path <= 1e-12:
        return None
    return net / path


def vol_60(bars: BarSeries, as_of: str) -> float | None:
    """60 日年化波动率（方向取负）。"""
    v = _std_ret(bars.close_up_to(as_of), 60)
    if v is None:
        return None
    return v * np.sqrt(252)


def downside_vol_60(bars: BarSeries, as_of: str) -> float | None:
    """60 日下行半方差年化（方向取负）。"""
    c = bars.close_up_to(as_of)
    if len(c) < 61:
        return None
    rets = c.pct_change().iloc[-60:].dropna()
    down = rets[rets < 0]
    if len(down) < 2:
        return None  # 样本不足不给 0（direction=-1 时 0 会排到最优）
    return float(np.sqrt((down ** 2).mean()) * np.sqrt(252))


QUALITY_FACTORS: list[FactorDef] = [
    FactorDef("eff_ratio_60", "60日效率比", eff_ratio_60, direction=1.0, default_weight=1.0),
    FactorDef("vol_60", "60日年化波动", vol_60, direction=-1.0, default_weight=0.8),
    FactorDef("downside_vol_60", "60日下行波动", downside_vol_60, direction=-1.0, default_weight=0.6),
]
