"""盘口异动标签维度：60日新高 / 60日大幅上涨 适当加分。"""

from __future__ import annotations

from quant.config import load_scoring_config
from quant.pool.pkyd_util import stock_pkyd_tags
from quant.scoring.context import ScoreContext
from quant.scoring.dimensions.base import clamp
from quant.scoring.models import DimensionResult


class PkydSignalScorer:
    name = "pkyd_signal"

    def score(self, ctx: ScoreContext, stock: dict) -> DimensionResult:
        tags = stock_pkyd_tags(stock)
        if not tags:
            return DimensionResult(self.name, 0, 0, True, available=False, detail={})

        cfg = (load_scoring_config().get("dimensions") or {}).get(self.name) or {}
        base = float(cfg.get("base_score", 55))
        per_tag = float(cfg.get("score_per_tag", 22))
        s = min(100.0, base + len(tags) * per_tag)
        return DimensionResult(
            self.name,
            clamp(s),
            0,
            True,
            detail={"盘口异动标签": tags},
        )
