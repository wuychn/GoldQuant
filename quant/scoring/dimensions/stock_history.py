"""个股历史行情维度。"""

from __future__ import annotations

from quant.scoring.context import ScoreContext
from quant.scoring.dimensions.base import clamp
from quant.scoring.models import DimensionResult


class StockHistoryScorer:
    name = "stock_history"

    def score(self, ctx: ScoreContext, stock: dict) -> DimensionResult:
        hist = stock.get("历史行情") or []
        if not isinstance(hist, list) or len(hist) < 5:
            return DimensionResult(self.name, 50, 0, True, available=False, detail={})
        recent = hist[-10:]
        ups = 0
        for row in recent:
            if not isinstance(row, dict):
                continue
            try:
                chg = float(row.get("涨跌幅", 0) or 0)
            except (TypeError, ValueError):
                chg = 0
            if chg > 0:
                ups += 1
        ratio = ups / max(1, len(recent))
        try:
            last_close = float(recent[-1].get("收盘", 0) or 0)
            first_close = float(recent[0].get("收盘", 0) or 0)
            trend = (last_close - first_close) / first_close * 100 if first_close else 0
        except (TypeError, ValueError):
            trend = 0
        s = ratio * 50 + max(-10, min(10, trend)) * 3 + 20
        return DimensionResult(
            self.name,
            clamp(s),
            0,
            True,
            detail={"近10日阳线占比": round(ratio, 2), "区间涨跌": round(trend, 2)},
        )
