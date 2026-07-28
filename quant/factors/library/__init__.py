"""因子库包：按族汇总。"""

from __future__ import annotations

from quant.factors.library.base import BarSeries, FactorDef
from quant.factors.library.fundamental import FUNDAMENTAL_FACTORS
from quant.factors.library.flow import FLOW_FACTORS
from quant.factors.library.hot import HOT_FACTORS
from quant.factors.library.momentum import MOMENTUM_FACTORS
from quant.factors.library.position import POSITION_FACTORS
from quant.factors.library.quality import QUALITY_FACTORS
from quant.factors.library.theme import THEME_FACTORS
from quant.factors.library.volume import VOLUME_FACTORS

ALL_FACTORS: list[FactorDef] = (
    MOMENTUM_FACTORS
    + QUALITY_FACTORS
    + FUNDAMENTAL_FACTORS
    + POSITION_FACTORS
    + VOLUME_FACTORS
    + FLOW_FACTORS
    + THEME_FACTORS
    + HOT_FACTORS
)

__all__ = [
    "BarSeries",
    "FactorDef",
    "ALL_FACTORS",
    "MOMENTUM_FACTORS",
    "QUALITY_FACTORS",
    "FUNDAMENTAL_FACTORS",
    "POSITION_FACTORS",
    "VOLUME_FACTORS",
    "FLOW_FACTORS",
    "THEME_FACTORS",
    "HOT_FACTORS",
]
