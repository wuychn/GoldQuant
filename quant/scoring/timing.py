"""择时评分器：盘中买卖信号。"""

from __future__ import annotations

from quant.config import load_scoring_config
from quant.scoring.engine import ScoringEngine, merge_dimension_overrides
from quant.scoring.models import StockScore


class TimingScorer(ScoringEngine):
    """Timing alpha：scoring.timing.dimensions deep-merge 全局 dimensions。"""

    def __init__(self, config: dict | None = None):
        base = config or load_scoring_config()
        timing = base.get("timing") or {}
        merged = {**base, **{k: v for k, v in timing.items() if k != "dimensions"}}
        merged["dimensions"] = merge_dimension_overrides(
            base.get("dimensions") or {},
            timing.get("dimensions") or {},
        )
        super().__init__(merged)

    def score_for_trade(self, ctx, stocks: list[dict]) -> list[StockScore]:
        return self.apply_threshold(self.score_many(ctx, stocks), kind="buy")
