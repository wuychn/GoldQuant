"""同花顺人气排名维度。"""

from __future__ import annotations

from quant.scoring.context import ScoreContext
from quant.scoring.dimensions.base import clamp
from quant.scoring.models import DimensionResult


def _popularity_rank(stock: dict, payload: dict) -> int | None:
    rank = stock.get("人气排名")
    if rank is not None:
        try:
            return int(rank)
        except (TypeError, ValueError):
            pass
    code = str(stock.get("股票代码", "")).strip()
    for row in payload.get("同花顺人气榜") or []:
        if str(row.get("股票代码", "")).strip() == code:
            try:
                return int(row.get("人气排名"))
            except (TypeError, ValueError):
                return None
    return None


class PopularityRankScorer:
    name = "popularity_rank"

    def score(self, ctx: ScoreContext, stock: dict) -> DimensionResult:
        rank = _popularity_rank(stock, ctx.payload)
        if rank is None:
            return DimensionResult(self.name, 40, 0, True, available=False, detail={})
        if rank <= 3:
            s = 100
        elif rank <= 10:
            s = 85
        elif rank <= 20:
            s = 65
        else:
            s = 35
        return DimensionResult(self.name, clamp(s), 0, True, detail={"人气排名": rank})
