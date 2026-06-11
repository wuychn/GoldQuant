"""全球宏观/地缘维度（智能盯盘；读 news 模式写入的缓存）。"""

from __future__ import annotations

from quant.config import load_scoring_config
from quant.scoring.context import ScoreContext
from quant.scoring.dimensions.base import clamp
from quant.scoring.global_macro import global_macro_for_scoring, sentiment_to_score
from quant.scoring.models import DimensionResult

_SENTIMENT_LABEL = {
    "bearish": "利空",
    "neutral": "中性",
    "bullish": "利好",
}


class GlobalMacroScorer:
    name = "global_macro"

    def score(self, ctx: ScoreContext, stock: dict) -> DimensionResult:
        cfg = (load_scoring_config().get("dimensions") or {}).get(self.name) or {}
        modes = cfg.get("modes") or ["during_market"]
        if ctx.mode and ctx.mode not in modes:
            return DimensionResult(self.name, 50, 0, True, available=False, detail={})

        entry = global_macro_for_scoring()
        if not entry:
            return DimensionResult(self.name, 50, 0, True, available=False, detail={})

        sentiment = entry["sentiment"]
        custom_scores = {
            "bearish": float(cfg.get("bearish_score", 5)),
            "neutral": float(cfg.get("neutral_score", 50)),
            "bullish": float(cfg.get("bullish_score", 95)),
        }
        score = sentiment_to_score(sentiment, scores=custom_scores)
        label = _SENTIMENT_LABEL.get(sentiment, sentiment)
        detail = {
            "判断": label,
            "理由": entry.get("reason") or "",
            "更新时间": entry.get("updated_at") or "",
        }
        return DimensionResult(self.name, clamp(score), 0, True, detail=detail)
