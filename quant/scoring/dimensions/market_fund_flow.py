"""大盘资金流维度。"""

from __future__ import annotations

from quant.scoring.context import ScoreContext
from quant.scoring.dimensions.base import clamp
from quant.scoring.models import DimensionResult


class MarketFundFlowScorer:
    name = "market_fund_flow"

    def score(self, ctx: ScoreContext, stock: dict) -> DimensionResult:
        rows = ctx.payload.get("大盘资金流") or []
        if not isinstance(rows, list) or not rows:
            return DimensionResult(self.name, 50, 0, True, available=False, detail={})
        latest = rows[-1] if isinstance(rows[-1], dict) else {}
        try:
            main = float(latest.get("主力净流入-净额", 0) or 0)
        except (TypeError, ValueError):
            main = 0.0
        if main > 0:
            s = 75
        elif main > -5e9:
            s = 55
        else:
            s = 25
        return DimensionResult(self.name, clamp(s), 0, True, detail={"主力净流入": main})
