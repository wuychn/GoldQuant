"""质量族：效率比 + 方向反转频率 + 波动率 + 下行波动。

eff_ratio（Kaufman 效率比）= |净涨幅| / Σ|日收益|，衡量趋势顺滑度。
flip_rate（方向反转频率）与 eff_ratio 正交：eff_ratio 对幅度敏感（大阴大阳拉低），
flip_rate 只看方向计数（小锯齿也能抓）——单边小阴阳串 eff_ratio 高/flip_rate 低，
锯齿震荡 eff_ratio 中/flip_rate 高（后者是 main_wave 想剔除的"上蹿下跳"）。
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


def flip_rate_60(bars: BarSeries, as_of: str) -> float | None:
    """60 日方向反转频率（方向取负：反转越少越看多）。与 eff_ratio 正交。"""
    c = bars.close_up_to(as_of)
    if len(c) < 61:
        return None
    rets = c.pct_change().iloc[-60:].dropna()
    if len(rets) < 3:
        return None
    signs = np.sign(rets.values)
    flips = int(np.sum(signs[1:] * signs[:-1] < 0))
    return flips / (len(signs) - 1)


def vol_60(bars: BarSeries, as_of: str) -> float | None:
    """60 日年化波动率（方向取负）。"""
    v = _std_ret(bars.close_up_to(as_of), 60)
    if v is None:
        return None
    return v * np.sqrt(252)


QUALITY_FACTORS: list[FactorDef] = [
    FactorDef("eff_ratio_60", "60日效率比", eff_ratio_60, direction=1.0, default_weight=1.0),
    FactorDef("flip_rate_60", "60日反转频率", flip_rate_60, direction=-1.0, default_weight=0.6),
    FactorDef("vol_60", "60日年化波动", vol_60, direction=-1.0, default_weight=0.8),
]
