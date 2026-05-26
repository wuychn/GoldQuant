"""大盘指数维度。"""

from __future__ import annotations

from quant.scoring.context import ScoreContext, index_change
from quant.scoring.dimensions.base import clamp
from quant.scoring.models import DimensionResult


class MarketIndexScorer:
    name = "market_index"

    def score(self, ctx: ScoreContext, stock: dict) -> DimensionResult:
        chg = index_change(ctx.payload)
        if chg is None:
            return DimensionResult(self.name, 50, 0, True, available=False, detail={})
        if chg > 1.0:
            s = 95
        elif chg > 0.5:
            s = 80
        elif chg > -0.5:
            s = 55
        elif chg > -1.0:
            s = 35
        else:
            s = 15
        return DimensionResult(self.name, clamp(s), 0, True, detail={"上证涨跌幅": chg})
