"""中性化合成 alpha 评分维度（依赖截面预计算）。"""

from __future__ import annotations

from quant.scoring.context import ScoreContext
from quant.scoring.models import DimensionResult


class NeutralAlphaScorer:
    """读取 ctx.neutral_alpha_scores[code]（0–100）；无预计算则不可用。"""

    name = "neutral_alpha"

    def score(self, ctx: ScoreContext, stock: dict) -> DimensionResult:
        code = str(stock.get("股票代码", "")).strip()
        scores = getattr(ctx, "neutral_alpha_scores", None) or {}
        if code and code in scores:
            return DimensionResult(
                name=self.name,
                score=float(scores[code]),
                weight=0.0,
                enabled=True,
                available=True,
                detail={"来源": "行业市值中性化因子合成"},
            )
        return DimensionResult(
            name=self.name,
            score=50.0,
            weight=0.0,
            enabled=True,
            available=False,
            detail={"原因": "无截面中性化预计算"},
        )
