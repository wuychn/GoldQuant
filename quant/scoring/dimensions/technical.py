"""技术指标维度。"""

from __future__ import annotations

from quant.scoring.context import ScoreContext
from quant.scoring.dimensions.base import clamp
from quant.scoring.models import DimensionResult


class TechnicalScorer:
    name = "technical"

    def score(self, ctx: ScoreContext, stock: dict) -> DimensionResult:
        t = stock.get("技术指标") or {}
        if not isinstance(t, dict) or not t:
            return DimensionResult(self.name, 50, 0, True, available=False, detail={})
        ma5 = t.get("MA5")
        ma10 = t.get("MA10")
        ma20 = t.get("MA20")
        pk = stock.get("盘口") if isinstance(stock.get("盘口"), dict) else {}
        last = pk.get("最新")
        try:
            last_f = float(last)
            ma5f = float(ma5)
            ma10f = float(ma10)
            ma20f = float(ma20)
        except (TypeError, ValueError):
            return DimensionResult(self.name, 50, 0, True, available=False, detail={})
        s = 40
        if last_f > ma5f > ma10f > ma20f:
            s = 95
        elif last_f > ma20f:
            s = 70
        elif last_f > ma20f * 0.98:
            s = 55
        else:
            s = 25
        macd = t.get("MACD")
        if macd is not None:
            try:
                if float(macd) > 0:
                    s = min(100, s + 10)
            except (TypeError, ValueError):
                pass
        return DimensionResult(self.name, clamp(s), 0, True, detail={"MA20": ma20, "最新": last})
