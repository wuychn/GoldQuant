"""概念权重共振维度。"""

from __future__ import annotations

from quant.candidates.candidate_config import PAYLOAD_KEY_POPULARITY
from quant.scoring.industry_aliases import expand_industries
from quant.scoring.context import ScoreContext
from quant.scoring.score_util import clamp
from quant.scoring.models import DimensionResult
from quant.scoring.theme_tracker import score_theme_resonance, theme_detail

CONCEPT_SOURCE_HOT = "同花顺"
CONCEPT_SOURCE_THS_FIT = "同花顺F10粘合度"
DISPLAY_CONCEPT_LIMIT = 3


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


def _parse_concept_fit_order(stock: dict) -> list[tuple[str, int]]:
    """解析概念粘合度顺序（rank 越小越相关）。"""
    raw = stock.get("概念粘合度")
    if isinstance(raw, list):
        out: list[tuple[str, int]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            concept = str(item.get("concept") or "").strip()
            rank = item.get("rank")
            if concept and isinstance(rank, int) and rank > 0:
                out.append((concept, rank))
        if out:
            out.sort(key=lambda x: (x[1], x[0]))
            return out
    src = str(stock.get("概念来源") or "").strip()
    concepts_raw = stock.get("所属概念")
    if src == CONCEPT_SOURCE_THS_FIT and isinstance(concepts_raw, list):
        ordered = [str(x).strip() for x in concepts_raw if str(x).strip()]
        if ordered:
            return [(name, idx + 1) for idx, name in enumerate(ordered)]
    return []


def _has_concept_fit_rank(stock: dict) -> bool:
    return bool(_parse_concept_fit_order(stock))


def _parse_name_list(raw: object) -> list[str]:
    if isinstance(raw, str):
        text = raw.strip()
        if not text or text in ("无", "-", "—"):
            return []
        return [x.strip() for x in text.replace(";", "、").replace(",", "、").split("、") if x.strip()]
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return []


def stock_concept_display_names(stock: dict, *, limit: int = DISPLAY_CONCEPT_LIMIT) -> list[str]:
    """推送展示用：优先概念粘合度 Top N，否则所属概念列表前 N 个。"""
    order = _parse_concept_fit_order(stock)
    if order:
        return [name for name, _ in order[: max(1, limit)]]
    return _parse_name_list(stock.get("所属概念") or stock.get("概念"))[: max(1, limit)]


def format_stock_concepts_brief(stock: dict, *, limit: int = DISPLAY_CONCEPT_LIMIT) -> str | None:
    """如「所属概念超级电容、储能、5G」。"""
    names = stock_concept_display_names(stock, limit=limit)
    if not names:
        return None
    return f"所属概念{'、'.join(names)}"


def resolve_stock_concepts(stock: dict, payload: dict) -> dict:
    """有概念粘合度时保留 THS 顺序；否则可用人气榜 tag 覆盖。"""
    if _has_concept_fit_rank(stock):
        return stock
    if str(stock.get("概念来源") or "").strip() == CONCEPT_SOURCE_THS_FIT:
        return stock
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
        fit_order = _parse_concept_fit_order(stock)
        concepts = _stock_concepts(stock)
        industries_raw = _stock_industry_raw(stock)
        industries = _stock_industry(stock)
        detail = theme_detail(ctx.payload, mode=ctx.mode)
        raw_score, hit_detail = score_theme_resonance(
            concepts,
            industries,
            ctx.payload,
            concept_fit_order=fit_order or None,
            mode=ctx.mode,
        )
        available = bool(hit_detail.get("available", True))
        mapped_only = sorted(industries - industries_raw)
        theme_tags = concepts | industries
        fit_preview = [
            {"rank": r, "concept": c}
            for c, r in (fit_order[:DISPLAY_CONCEPT_LIMIT] if fit_order else [])
        ]
        return DimensionResult(
            self.name,
            clamp(raw_score, lo=-100.0, hi=100.0),
            0,
            True,
            available=available,
            detail={
                **detail,
                **hit_detail,
                "个股概念": (
                    [c for c, _ in fit_order[:DISPLAY_CONCEPT_LIMIT]]
                    if fit_order
                    else list(concepts)[:DISPLAY_CONCEPT_LIMIT]
                ),
                "概念粘合度": fit_preview or None,
                "个股行业": sorted(industries_raw),
                "个股行业映射": mapped_only,
                "个股题材": sorted(theme_tags)[:16],
                "概念来源": str(stock.get("概念来源") or "").strip() or None,
            },
        )
