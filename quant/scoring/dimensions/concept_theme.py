"""概念权重共振维度。"""

from __future__ import annotations

from quant.pool.candidate_config import PAYLOAD_KEY_POPULARITY
from quant.scoring.industry_aliases import expand_industries
from quant.scoring.context import ScoreContext
from quant.scoring.dimensions.base import clamp
from quant.scoring.models import DimensionResult
from quant.scoring.theme_tracker import score_theme_resonance, theme_detail

CONCEPT_SOURCE_HOT = "同花顺"


def _parse_name_set(raw: object) -> set[str]:
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw or raw in ("无", "-", "—"):
            return set()
        return {x.strip() for x in raw.replace(";", "、").replace(",", "、").split("、") if x.strip()}
    if isinstance(raw, list):
        return {str(x).strip() for x in raw if str(x).strip()}
    return set()


def _stock_concepts(stock: dict) -> set[str]:
    """同花顺概念名（不做别名映射）。"""
    return _parse_name_set(stock.get("所属概念") or stock.get("概念"))


def _stock_industry_raw(stock: dict) -> set[str]:
    return _parse_name_set(stock.get("行业"))


def _stock_industry(stock: dict) -> set[str]:
    """东财 jbxx 行业 → 同花顺行业榜名（仅行业域映射）。"""
    return expand_industries(_stock_industry_raw(stock))


def resolve_stock_concepts(stock: dict, payload: dict) -> dict:
    """所属概念优先同花顺人气榜 tag（与概念榜同源）。"""
    code = str(stock.get("股票代码", "")).strip()
    if code:
        for row in payload.get(PAYLOAD_KEY_POPULARITY) or []:
            if not isinstance(row, dict):
                continue
            if str(row.get("股票代码", "")).strip() != code:
                continue
            tag = row.get("所属概念") or row.get("概念")
            if tag:
                return {**stock, "所属概念": tag, "概念来源": CONCEPT_SOURCE_HOT}
    return stock


class ConceptThemeScorer:
    name = "concept_theme"

    def score(self, ctx: ScoreContext, stock: dict) -> DimensionResult:
        stock = resolve_stock_concepts(stock, ctx.payload)
        concepts = _stock_concepts(stock)
        industries_raw = _stock_industry_raw(stock)
        industries = _stock_industry(stock)
        detail = theme_detail(ctx.payload, mode=ctx.mode)
        raw_score, hit_detail = score_theme_resonance(
            concepts,
            industries,
            ctx.payload,
            mode=ctx.mode,
        )
        available = bool(hit_detail.get("available", True))
        mapped_only = sorted(industries - industries_raw)
        theme_tags = concepts | industries
        return DimensionResult(
            self.name,
            clamp(raw_score, lo=-100.0, hi=100.0),
            0,
            True,
            available=available,
            detail={
                **detail,
                **hit_detail,
                "个股概念": list(concepts)[:12],
                "个股行业": sorted(industries_raw),
                "个股行业映射": mapped_only,
                "个股题材": sorted(theme_tags)[:16],
                "概念来源": str(stock.get("概念来源") or "").strip() or None,
            },
        )
