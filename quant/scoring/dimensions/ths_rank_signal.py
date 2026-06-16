"""同花顺形态榜维度：创新高 / 持续上涨 / 持续放量 / 量价齐升 适当加分。"""

from __future__ import annotations

from quant.config import load_scoring_config
from quant.pool.ths_rank_util import stock_ths_rank_tags
from quant.scoring.context import ScoreContext
from quant.scoring.dimensions.base import clamp
from quant.scoring.models import DimensionResult


class ThsRankSignalScorer:
    name = "ths_rank_signal"

    def score(self, ctx: ScoreContext, stock: dict) -> DimensionResult:
        del ctx
        tags = stock_ths_rank_tags(stock)
        if not tags:
            return DimensionResult(self.name, 0, 0, True, available=False, detail={})

        cfg = (load_scoring_config().get("dimensions") or {}).get(self.name) or {}
        base = float(cfg.get("base_score", 35))
        per_tag = float(cfg.get("score_per_tag", 5))
        s = min(100.0, base + len(tags) * per_tag)
        return DimensionResult(
            self.name,
            clamp(s),
            0,
            True,
            detail={"榜单标签": tags},
        )
