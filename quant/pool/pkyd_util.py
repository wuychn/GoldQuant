"""盘口异动：标签索引、概念共振候选、与人气/涨停交叉打标。"""

from __future__ import annotations

from typing import Any

from app.utils.common_util import (
    extract_stock_code,
    filter_symbol_pool_rows,
    is_allowed_symbol_pool_code,
    row_allowed_symbol_pool,
)
from quant.config import load_gates_config
from quant.scoring.dimensions.concept_theme import _stock_concepts, resolve_stock_concepts
from quant.scoring.theme_tracker import resolve_main_themes, snapshot_boards

_PKYD_TAG_KEYS = ("盘口异动标签", "异动类型", "原因")


def extract_pkyd_code(row: dict | None) -> str:
    """从盘口异动行提取并规范化为 6 位 A 股代码。"""
    return extract_stock_code(row)


def pkyd_row_allowed(row: dict | None) -> bool:
    """仅沪主/深主/创业板；剔除科创板(688/689)、北交所(8/4) 等。"""
    return row_allowed_symbol_pool(row)


def filter_pkyd_rows(rows: list[dict] | None) -> list[dict]:
    """过滤并规范化 ``盘口异动`` 列表（统一 ``股票代码`` 字段）。"""
    return filter_symbol_pool_rows(rows)


def _code(row: dict) -> str:
    return extract_pkyd_code(row)


def _normalize_tags(raw: object) -> list[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str):
        s = raw.strip()
        return [s] if s else []
    return []


def merge_pkyd_rows_by_code(
    *,
    entries: list[tuple[str, str | None, str]] | None = None,
    rows: list[dict] | None = None,
) -> list[dict]:
    """按股票代码合并盘口异动记录，同一代码只保留一条并合并 ``异动类型``。"""
    by_code: dict[str, dict[str, Any]] = {}

    for code, name, label in entries or []:
        code = normalize_a_share_code(code)
        label = str(label).strip()
        if not code or not is_allowed_symbol_pool_code(code):
            continue
        item = by_code.setdefault(
            code,
            {"股票代码": code, "股票名称": name, "异动类型": []},
        )
        if name and not item.get("股票名称"):
            item["股票名称"] = name
        tags: list[str] = item["异动类型"]
        if label and label not in tags:
            tags.append(label)

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        code = _code(row)
        if not code or not is_allowed_symbol_pool_code(code):
            continue
        item = by_code.setdefault(
            code,
            {
                "股票代码": code,
                "股票名称": row.get("股票名称") or row.get("名称"),
                "异动类型": [],
            },
        )
        name = row.get("股票名称") or row.get("名称")
        if name and not item.get("股票名称"):
            item["股票名称"] = name
        tags = item["异动类型"]
        for tag in _normalize_tags(row.get("异动类型")) or _normalize_tags(row.get("原因")):
            if tag not in tags:
                tags.append(tag)
        for field in ("所属概念", "概念来源"):
            if row.get(field) is not None:
                item[field] = row[field]

    out: list[dict] = []
    for item in by_code.values():
        tags = item.get("异动类型") or []
        item["原因"] = tags[0] if len(tags) == 1 else "、".join(tags)
        out.append(item)
    return filter_pkyd_rows(out)


def build_pkyd_tag_map(pkyd_rows: list[dict] | None) -> dict[str, list[str]]:
    """股票代码 → 异动类型列表（如 60日新高 / 60日大幅上涨）。"""
    out: dict[str, set[str]] = {}
    for row in pkyd_rows or []:
        if not isinstance(row, dict):
            continue
        code = _code(row)
        if not code or not is_allowed_symbol_pool_code(code):
            continue
        tags = set(_normalize_tags(row.get("异动类型")))
        if not tags:
            tags = set(_normalize_tags(row.get("原因")))
        if not tags:
            continue
        out.setdefault(code, set()).update(tags)
    return {k: sorted(v) for k, v in out.items()}


def attach_pkyd_tags(row: dict, tag_map: dict[str, list[str]]) -> dict:
    code = _code(row)
    tags = tag_map.get(code)
    if not tags:
        return row
    merged = sorted(set(_normalize_tags(row.get("盘口异动标签")) + tags))
    return {**row, "盘口异动标签": merged}


def enrich_list_with_pkyd_tags(rows: list[dict] | None, tag_map: dict[str, list[str]]) -> list[dict]:
    if not rows:
        return []
    return [attach_pkyd_tags(r, tag_map) if isinstance(r, dict) else r for r in rows]


def enrich_zt_stats_with_pkyd(zt_stats: dict | None, tag_map: dict[str, list[str]]) -> dict:
    if not isinstance(zt_stats, dict):
        return zt_stats or {}
    out = dict(zt_stats)
    for key in ("今日涨停", "昨日涨停"):
        pool = out.get(key)
        if isinstance(pool, list):
            out[key] = enrich_list_with_pkyd_tags(pool, tag_map)
    return out


def hot_concept_targets(payload: dict) -> set[str]:
    """近期确认主线 ∪ 当日涨幅榜前十 ∪ 资金流入榜前十。"""
    cfg = load_gates_config().get("main_theme") or {}
    limit = int(cfg.get("board_limit", 10))
    gain, fund = snapshot_boards(payload, limit=limit)
    main = resolve_main_themes(payload)
    return main | gain | fund


def pkyd_row_matches_hot_concepts(row: dict, payload: dict) -> bool:
    targets = hot_concept_targets(payload)
    if not targets:
        return False
    row = resolve_stock_concepts(row, payload)
    return bool(_stock_concepts(row) & targets)


def stock_pkyd_tags(stock: dict) -> list[str]:
    for key in _PKYD_TAG_KEYS:
        tags = _normalize_tags(stock.get(key))
        if tags:
            return tags
    return []
