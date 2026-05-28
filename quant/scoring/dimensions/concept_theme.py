"""概念主线共振维度。"""

from __future__ import annotations

from quant.scoring.context import ScoreContext
from quant.scoring.dimensions.base import clamp
from quant.scoring.models import DimensionResult
from quant.scoring.theme_tracker import resolve_main_themes, score_concept_resonance, theme_detail

CONCEPT_SOURCE_WENCAI = "问财"
CONCEPT_SOURCE_HOT = "人气榜"


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


def _concept_source(stock: dict) -> str:
    return str(stock.get("概念来源") or "").strip()


def _has_wencai_concepts(stock: dict) -> bool:
    return _concept_source(stock) == CONCEPT_SOURCE_WENCAI and bool(_stock_concepts(stock))


def resolve_stock_concepts(stock: dict, payload: dict) -> dict:
    """优先问财所属概念；仅当缺失时回退同花顺人气榜 tag。"""
    if _has_wencai_concepts(stock):
        return stock
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
            return {**stock, "所属概念": tag, "概念来源": CONCEPT_SOURCE_HOT}
    return stock


# 兼容旧引用
attach_concepts_from_hot = resolve_stock_concepts


class ConceptThemeScorer:
    name = "concept_theme"

    def score(self, ctx: ScoreContext, stock: dict) -> DimensionResult:
        stock = resolve_stock_concepts(stock, ctx.payload)
        concepts = _stock_concepts(stock)
        detail = theme_detail(ctx.payload)
        main = resolve_main_themes(ctx.payload)
        if not main:
            return DimensionResult(self.name, 50, 0, True, available=False, detail=detail)

        raw_score, hit_detail = score_concept_resonance(concepts, ctx.payload)
        src = _concept_source(stock)
        return DimensionResult(
            self.name,
            clamp(raw_score),
            0,
            True,
            detail={
                **detail,
                **hit_detail,
                "个股概念": list(concepts)[:12],
                "概念来源": src or None,
            },
        )
