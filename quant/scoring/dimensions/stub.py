"""预留维度（数据暂不可用）。"""

from __future__ import annotations

from quant.scoring.context import ScoreContext
from quant.scoring.models import DimensionResult


class StubScorer:
    def __init__(self, name: str):
        self.name = name

    def score(self, ctx: ScoreContext, stock: dict) -> DimensionResult:
        return DimensionResult(self.name, 50, 0, True, available=False, detail={"status": "stub"})
