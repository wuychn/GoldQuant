"""晚间候选池：同花顺人气榜 + 主线概念（主升浪龙头候选）。"""

from __future__ import annotations

from quant.config import load_scoring_config
from quant.scoring.dimensions.concept_theme import _stock_concepts, attach_concepts_from_hot
from quant.scoring.theme_tracker import resolve_main_themes


def _code(row: dict) -> str:
    return str(row.get("股票代码") or row.get("代码") or "").strip()


def build_candidates(payload: dict) -> list[dict]:
    cfg = load_scoring_config().get("candidate") or {}
    limit = int(cfg.get("popularity_limit", 20))
    include_zt = bool(cfg.get("include_zt_pool", False))
    tops = resolve_main_themes(payload)

    merged: dict[str, dict] = {}

    for row in (payload.get("同花顺人气榜") or [])[:limit]:
        if not isinstance(row, dict):
            continue
        code = _code(row)
        if not code:
            continue
        merged[code] = dict(row)

    # 概念涨幅/资金榜前列个股补充（若在 enrich 列表中）
    for key in ("自选股", "同花顺人气榜"):
        for row in payload.get(key) or []:
            if not isinstance(row, dict):
                continue
            code = _code(row)
            if not code:
                continue
            row = attach_concepts_from_hot(row, payload)
            concepts = _stock_concepts(row)
            if tops and concepts & tops:
                merged[code] = {**merged.get(code, {}), **row}

    if include_zt:
        zt = payload.get("涨停统计") or payload.get("涨停概况") or {}
        pool = zt.get("今日涨停") if isinstance(zt, dict) else []
        for row in pool or []:
            if not isinstance(row, dict):
                continue
            code = _code(row)
            if code and code not in merged:
                merged[code] = {"股票代码": code, "股票名称": row.get("名称", ""), **row}

    return list(merged.values())
