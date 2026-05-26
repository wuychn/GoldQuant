"""评分维度基类。"""

from __future__ import annotations

from typing import Protocol

from quant.scoring.context import ScoreContext
from quant.scoring.models import DimensionResult


class DimensionScorer(Protocol):
    name: str

    def score(self, ctx: ScoreContext, stock: dict) -> DimensionResult: ...


def clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))
