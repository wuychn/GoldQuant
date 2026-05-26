"""个股资金流维度。"""

from __future__ import annotations

import re

from quant.scoring.context import ScoreContext
from quant.scoring.dimensions.base import clamp
from quant.scoring.models import DimensionResult


def _parse_amount(v: object) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    m = re.search(r"-?\d+(?:\.\d+)?", s.replace(",", ""))
    if not m:
        return None
    return float(m.group())


class StockFundFlowScorer:
    name = "stock_fund_flow"

    def score(self, ctx: ScoreContext, stock: dict) -> DimensionResult:
        flow = stock.get("个股资金流") or {}
        if not isinstance(flow, dict) or not flow:
            return DimensionResult(self.name, 50, 0, True, available=False, detail={})
        net = _parse_amount(flow.get("净额"))
        if net is None:
            return DimensionResult(self.name, 50, 0, True, available=False, detail={})
        if net > 0:
            s = 80
        elif net > -1000:
            s = 50
        else:
            s = 25
        return DimensionResult(self.name, clamp(s), 0, True, detail={"净额": net})
