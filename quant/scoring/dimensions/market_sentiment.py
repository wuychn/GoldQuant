"""赚钱效应维度。"""

from __future__ import annotations

from quant.scoring.context import ScoreContext
from quant.scoring.dimensions.base import clamp
from quant.scoring.models import DimensionResult


class MarketSentimentScorer:
    name = "market_sentiment"

    def score(self, ctx: ScoreContext, stock: dict) -> DimensionResult:
        p = ctx.payload.get("赚钱效应") or {}
        up = int(p.get("上涨", 0) or 0)
        down = int(p.get("下跌", 0) or 0)
        zt = int(p.get("涨停", 0) or 0)
        if up + down == 0:
            return DimensionResult(self.name, 50, 0, True, available=False, detail={})
        ratio = up / max(1, up + down)
        s = ratio * 70 + min(zt, 80) / 80 * 30
        return DimensionResult(
            self.name,
            clamp(s),
            0,
            True,
            detail={"上涨": up, "下跌": down, "涨停": zt},
        )
