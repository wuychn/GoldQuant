"""概念共振硬过滤（已弃用：概念强弱由 scoring.concept_theme 打分，pipeline 不再调用）。"""

from __future__ import annotations

from quant.pool.pkyd_util import hot_concept_targets
from quant.scoring.dimensions.concept_theme import _stock_concepts, resolve_stock_concepts


def matches_main_theme_concepts(stock: dict, payload: dict) -> bool:
    """问财/人气概念是否与当日主线目标集合有交集（遗留，供兼容引用）。"""
    targets = hot_concept_targets(payload)
    if not targets:
        return False
    row = resolve_stock_concepts(stock, payload)
    return bool(_stock_concepts(row) & targets)


def filter_concept_resonance(rows: list[dict], payload: dict) -> list[dict]:
    """遗留硬过滤；新链路请使用 ``quant.pool.pipeline.run_candidate_pipeline``。"""
    return [r for r in rows if matches_main_theme_concepts(r, payload)]
