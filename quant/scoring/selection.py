"""选股评分器：晚间候选池 / 自选 hysteresis。"""

from __future__ import annotations

from quant.config import load_scoring_config
from quant.scoring.engine import ScoringEngine, merge_dimension_overrides
from quant.scoring.models import StockScore


_SELECTION_KIND = "watchlist"


class SelectionScorer(ScoringEngine):
    """选股 alpha：scoring.selection.dimensions deep-merge 全局 dimensions。"""

    def __init__(self, config: dict | None = None):
        base = config or load_scoring_config()
        sel = base.get("selection") or {}
        merged = {**base, **{k: v for k, v in sel.items() if k != "dimensions"}}
        merged["dimensions"] = merge_dimension_overrides(
            base.get("dimensions") or {},
            sel.get("dimensions") or {},
        )
        super().__init__(merged)

    def score_for_watchlist(self, ctx, stocks: list[dict]) -> list[StockScore]:
        return self.apply_threshold(self.score_many(ctx, stocks), kind=_SELECTION_KIND)
