"""概念主线共振维度。"""

from __future__ import annotations

from quant.scoring.context import ScoreContext
from quant.scoring.dimensions.base import clamp
from quant.scoring.models import DimensionResult
from quant.scoring.theme_tracker import resolve_main_themes, theme_detail


def _stock_concepts(stock: dict) -> set[str]:
    raw = stock.get("所属概念") or stock.get("概念") or []
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw or raw in ("无", "-", "—"):
            return set()
        return {x.strip() for x in raw.replace(";", "、").replace(",", "、").split("、") if x.strip()}
    if isinstance(raw, list):
        return {str(x).strip() for x in raw if str(x).strip()}
    return set()


def attach_concepts_from_hot(stock: dict, payload: dict) -> dict:
    """持仓/自选若无所属概念，尝试从同花顺人气榜合并。"""
    if _stock_concepts(stock):
        return stock
    code = str(stock.get("股票代码", "")).strip()
    if not code:
        return stock
    for row in payload.get("同花顺人气榜") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("股票代码", "")).strip() != code:
            continue
        tag = row.get("所属概念") or row.get("概念")
        if tag:
            return {**stock, "所属概念": tag}
    return stock


class ConceptThemeScorer:
    name = "concept_theme"

    def score(self, ctx: ScoreContext, stock: dict) -> DimensionResult:
        stock = attach_concepts_from_hot(stock, ctx.payload)
        main = resolve_main_themes(ctx.payload)
        concepts = _stock_concepts(stock)
        detail = theme_detail(ctx.payload)
        if not main:
            return DimensionResult(self.name, 50, 0, True, available=False, detail=detail)
        hit = concepts & main
        if hit:
            s = min(100, 60 + len(hit) * 15)
        else:
            s = 25
        return DimensionResult(
            self.name,
            clamp(s),
            0,
            True,
            detail={**detail, "命中概念": list(hit), "个股概念": list(concepts)[:8]},
        )
