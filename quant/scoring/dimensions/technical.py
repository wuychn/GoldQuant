"""技术指标维度。"""

from __future__ import annotations

from quant.scoring.context import ScoreContext
from quant.scoring.dimensions.base import clamp
from quant.scoring.models import DimensionResult
from quant.scoring.tech_indicators import parse_technical_indicators, quote_last_price


class TechnicalScorer:
    name = "technical"

    def score(self, ctx: ScoreContext, stock: dict) -> DimensionResult:
        ti = parse_technical_indicators(stock.get("技术指标"))
        ma5, ma10, ma20 = ti["ma5"], ti["ma10"], ti["ma20"]
        if None in (ma5, ma10, ma20):
            return DimensionResult(self.name, 50, 0, True, available=False, detail={})
        last = quote_last_price(stock)
        if last is None or last <= 0:
            return DimensionResult(self.name, 50, 0, True, available=False, detail={})
        s = 40
        if last > ma5 > ma10 > ma20:
            s = 95
        elif last > ma20:
            s = 70
        elif last > ma20 * 0.98:
            s = 55
        else:
            s = 25
        macd = ti["macd"]
        if macd is not None and macd > 0:
            s = min(100, s + 10)
        return DimensionResult(
            self.name,
            clamp(s),
            0,
            True,
            detail={"MA5": ma5, "MA10": ma10, "MA20": ma20, "最新": last},
        )
