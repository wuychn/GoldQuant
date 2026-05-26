"""涨停统计维度。"""

from __future__ import annotations

from quant.scoring.context import ScoreContext, find_in_zt_pool, zt_height, zt_pool
from quant.scoring.dimensions.base import clamp
from quant.scoring.models import DimensionResult


class ZtCountScorer:
    name = "zt_count"

    def score(self, ctx: ScoreContext, stock: dict) -> DimensionResult:
        profit = ctx.payload.get("赚钱效应") or {}
        cnt = int(profit.get("涨停", len(zt_pool(ctx.payload))) or 0)
        if cnt >= 60:
            s = 95
        elif cnt >= 30:
            s = 65
        else:
            s = 30
        return DimensionResult(self.name, clamp(s), 0, True, detail={"涨停家数": cnt})


class ZtHeightScorer:
    name = "zt_height"

    def score(self, ctx: ScoreContext, stock: dict) -> DimensionResult:
        code = str(stock.get("股票代码", "")).strip()
        row = find_in_zt_pool(code, ctx.payload)
        market_h = zt_height(ctx.payload)
        boards = 0
        if row:
            try:
                boards = int(float(row.get("连板数", 0) or 0))
            except (TypeError, ValueError):
                boards = 0
        if boards >= market_h and boards >= 3:
            s = 95
        elif boards >= 2:
            s = 75
        elif boards == 1:
            s = 50
        else:
            s = 20
        return DimensionResult(
            self.name,
            clamp(s),
            0,
            True,
            detail={"连板数": boards, "市场高度": market_h},
        )
