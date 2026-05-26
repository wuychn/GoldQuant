"""概念主线共振维度。"""

from __future__ import annotations

from quant.scoring.context import ScoreContext
from quant.scoring.dimensions.base import clamp
from quant.scoring.models import DimensionResult


def _top_concepts(payload: dict, limit: int = 10) -> set[str]:
    boards = payload.get("概念板块") or {}
    names: set[str] = set()
    for key in ("涨幅榜", "资金流入榜"):
        for row in (boards.get(key) or [])[:limit]:
            if isinstance(row, dict):
                n = str(row.get("行业", "")).strip()
                if n:
                    names.add(n)
    return names


def _stock_concepts(stock: dict) -> set[str]:
    raw = stock.get("所属概念") or stock.get("概念") or []
    if isinstance(raw, str):
        return {x.strip() for x in raw.replace(";", "、").split("、") if x.strip()}
    if isinstance(raw, list):
        return {str(x).strip() for x in raw if str(x).strip()}
    return set()


class ConceptThemeScorer:
    name = "concept_theme"

    def score(self, ctx: ScoreContext, stock: dict) -> DimensionResult:
        tops = _top_concepts(ctx.payload)
        concepts = _stock_concepts(stock)
        if not tops:
            return DimensionResult(self.name, 50, 0, True, available=False, detail={})
        hit = concepts & tops
        if hit:
            s = min(100, 60 + len(hit) * 15)
        else:
            s = 25
        return DimensionResult(
            self.name,
            clamp(s),
            0,
            True,
            detail={"命中概念": list(hit), "主线概念数": len(tops)},
        )
