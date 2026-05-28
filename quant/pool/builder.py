"""晚间候选池：同花顺人气榜 + 主线概念 + 盘口异动概念共振。"""

from __future__ import annotations

from quant.config import load_scoring_config
from quant.pool.pkyd_util import (
    attach_pkyd_tags,
    build_pkyd_tag_map,
    merge_pkyd_rows_by_code,
    pkyd_row_matches_hot_concepts,
    stock_pkyd_tags,
)
from quant.scoring.dimensions.concept_theme import _stock_concepts, resolve_stock_concepts
from quant.scoring.theme_tracker import resolve_main_themes


def _code(row: dict) -> str:
    return str(row.get("股票代码") or row.get("代码") or "").strip()


def build_candidates(payload: dict) -> list[dict]:
    cfg = load_scoring_config().get("candidate") or {}
    limit = int(cfg.get("popularity_limit", 20))
    include_zt = bool(cfg.get("include_zt_pool", False))
    include_pkyd = bool(cfg.get("include_pkyd_concept_match", True))
    tops = resolve_main_themes(payload)
    tag_map = build_pkyd_tag_map(payload.get("盘口异动"))
    pkyd_rows = merge_pkyd_rows_by_code(rows=payload.get("盘口异动"))

    merged: dict[str, dict] = {}

    for row in (payload.get("同花顺人气榜") or [])[:limit]:
        if not isinstance(row, dict):
            continue
        code = _code(row)
        if not code:
            continue
        merged[code] = attach_pkyd_tags(dict(row), tag_map)

    # 概念涨幅/资金榜前列个股补充（若在 enrich 列表中）
    for key in ("自选股", "同花顺人气榜"):
        for row in payload.get(key) or []:
            if not isinstance(row, dict):
                continue
            code = _code(row)
            if not code:
                continue
            row = resolve_stock_concepts(row, payload)
            concepts = _stock_concepts(row)
            if tops and concepts & tops:
                merged[code] = attach_pkyd_tags({**merged.get(code, {}), **row}, tag_map)

    if include_pkyd:
        for row in pkyd_rows:
            if not isinstance(row, dict):
                continue
            code = _code(row)
            if not code or code in merged:
                continue
            if not pkyd_row_matches_hot_concepts(row, payload):
                continue
            tags = tag_map.get(code) or stock_pkyd_tags(row)
            merged[code] = {
                **row,
                "盘口异动标签": tags,
                "候选来源": "盘口异动",
            }

    if include_zt:
        zt = payload.get("涨停统计") or payload.get("涨停概况") or {}
        pool = zt.get("今日涨停") if isinstance(zt, dict) else []
        for row in pool or []:
            if not isinstance(row, dict):
                continue
            code = _code(row)
            if code and code not in merged:
                merged[code] = attach_pkyd_tags(
                    {"股票代码": code, "股票名称": row.get("名称", ""), **row},
                    tag_map,
                )

    return list(merged.values())
