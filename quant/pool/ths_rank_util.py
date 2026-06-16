"""同花顺形态榜：创新高 / 持续上涨 / 持续放量 / 量价齐升 标签合并与交叉打标。"""

from __future__ import annotations

from typing import Any

from app.utils.common_util import (
    extract_stock_code,
    filter_symbol_pool_rows,
    is_allowed_symbol_pool_code,
    normalize_a_share_code,
)

_TAG_KEYS = ("榜单标签", "异动类型", "原因")

CATEGORY_CXG = "创新高"
CATEGORY_LXSZ = "持续上涨"
CATEGORY_CXFL = "持续放量"
CATEGORY_LJQS = "量价齐升"

CXG_SUB_TAGS = frozenset({"创月新高", "半年新高", "一年新高", "历史新高"})
THS_RANK_CATEGORY_TAGS = frozenset(
    {CATEGORY_LXSZ, CATEGORY_CXFL, CATEGORY_LJQS, *CXG_SUB_TAGS}
)

_CATEGORY_ORDER = (CATEGORY_CXG, CATEGORY_LXSZ, CATEGORY_CXFL, CATEGORY_LJQS)


def tag_category(tag: str) -> str:
    t = str(tag).strip()
    if t in CXG_SUB_TAGS:
        return CATEGORY_CXG
    if t in (CATEGORY_LXSZ, CATEGORY_CXFL, CATEGORY_LJQS):
        return t
    return t


def extract_ths_rank_code(row: dict | None) -> str:
    return extract_stock_code(row)


def _normalize_tags(raw: object) -> list[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str):
        s = raw.strip()
        return [s] if s else []
    return []


def stock_ths_rank_tags(stock: dict) -> list[str]:
    for key in _TAG_KEYS:
        tags = _normalize_tags(stock.get(key))
        if tags:
            return tags
    return []


def format_ths_rank_watchlist_reason(tags: list[str] | None) -> str:
    """按形态类别分组，如：创新高(创月新高、半年新高)；量价齐升。"""
    grouped: dict[str, list[str]] = {}
    for tag in tags or []:
        t = str(tag).strip()
        if not t:
            continue
        cat = tag_category(t)
        bucket = grouped.setdefault(cat, [])
        if cat == CATEGORY_CXG:
            if t not in bucket:
                bucket.append(t)
        elif t not in bucket:
            bucket.append(t)

    parts: list[str] = []
    for cat in _CATEGORY_ORDER:
        sub = grouped.get(cat)
        if not sub:
            continue
        if cat == CATEGORY_CXG:
            parts.append(f"{cat}({'、'.join(sub)})")
        elif len(sub) == 1 and sub[0] == cat:
            parts.append(cat)
        else:
            parts.append(f"{cat}({'、'.join(sub)})")
    return "；".join(parts)


def merge_ths_rank_rows_by_code(
    *,
    entries: list[tuple[str, str | None, str]] | None = None,
    rows: list[dict] | None = None,
) -> list[dict]:
    """按股票代码合并形态榜记录，合并 ``榜单标签``。"""
    by_code: dict[str, dict[str, Any]] = {}

    for code, name, label in entries or []:
        code = normalize_a_share_code(code)
        label = str(label).strip()
        if not code or not is_allowed_symbol_pool_code(code):
            continue
        item = by_code.setdefault(
            code,
            {"股票代码": code, "股票名称": name, "榜单标签": []},
        )
        if name and not item.get("股票名称"):
            item["股票名称"] = name
        tags: list[str] = item["榜单标签"]
        if label and label not in tags:
            tags.append(label)

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        code = extract_ths_rank_code(row)
        if not code or not is_allowed_symbol_pool_code(code):
            continue
        item = by_code.setdefault(
            code,
            {
                "股票代码": code,
                "股票名称": row.get("股票名称") or row.get("股票简称") or row.get("名称"),
                "榜单标签": [],
            },
        )
        name = row.get("股票名称") or row.get("股票简称") or row.get("名称")
        if name and not item.get("股票名称"):
            item["股票名称"] = name
        tags = item["榜单标签"]
        for tag in _normalize_tags(row.get("榜单标签")):
            if tag not in tags:
                tags.append(tag)
        for field, value in row.items():
            if field in ("股票代码", "代码", "股票名称", "股票简称", "名称", "榜单标签"):
                continue
            if value is not None and field not in item:
                item[field] = value

    out: list[dict] = []
    for item in by_code.values():
        tags = item.get("榜单标签") or []
        item["原因"] = tags[0] if len(tags) == 1 else "、".join(tags)
        out.append(item)
    return filter_symbol_pool_rows(out)


def build_ths_rank_tag_map(rows: list[dict] | None) -> dict[str, list[str]]:
    out: dict[str, set[str]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        code = extract_ths_rank_code(row)
        if not code or not is_allowed_symbol_pool_code(code):
            continue
        tags = set(_normalize_tags(row.get("榜单标签")))
        if not tags:
            tags = set(_normalize_tags(row.get("原因")))
        if not tags:
            continue
        out.setdefault(code, set()).update(tags)
    return {k: sorted(v) for k, v in out.items()}


def attach_ths_rank_tags(row: dict, tag_map: dict[str, list[str]]) -> dict:
    code = extract_ths_rank_code(row)
    tags = tag_map.get(code)
    if not tags:
        return row
    merged = sorted(set(_normalize_tags(row.get("榜单标签")) + tags))
    return {**row, "榜单标签": merged}


def enrich_list_with_ths_rank_tags(
    rows: list[dict] | None,
    tag_map: dict[str, list[str]],
) -> list[dict]:
    if not rows:
        return []
    return [attach_ths_rank_tags(r, tag_map) if isinstance(r, dict) else r for r in rows]


def enrich_zt_stats_with_ths_rank(
    zt_stats: dict | None,
    tag_map: dict[str, list[str]],
) -> dict:
    if not isinstance(zt_stats, dict):
        return zt_stats or {}
    out = dict(zt_stats)
    for key in ("今日涨停", "昨日涨停"):
        pool = out.get(key)
        if isinstance(pool, list):
            out[key] = enrich_list_with_ths_rank_tags(pool, tag_map)
    return out


def split_enriched_ths_rank_payload(rows: list[dict]) -> dict[str, list[dict]]:
    """enrich 后按 payload 口径拆成四榜（同票可出现在多榜）。"""
    from quant.pool.candidate_config import (
        PAYLOAD_KEY_CXFL,
        PAYLOAD_KEY_CXG,
        PAYLOAD_KEY_LJQS,
        PAYLOAD_KEY_LXSZ,
    )

    out: dict[str, list[dict]] = {
        PAYLOAD_KEY_CXG: [],
        PAYLOAD_KEY_LXSZ: [],
        PAYLOAD_KEY_CXFL: [],
        PAYLOAD_KEY_LJQS: [],
    }
    for row in rows:
        if not isinstance(row, dict):
            continue
        tags = set(stock_ths_rank_tags(row))
        if tags & CXG_SUB_TAGS:
            out[PAYLOAD_KEY_CXG].append(row)
        if CATEGORY_LXSZ in tags:
            out[PAYLOAD_KEY_LXSZ].append(row)
        if CATEGORY_CXFL in tags:
            out[PAYLOAD_KEY_CXFL].append(row)
        if CATEGORY_LJQS in tags:
            out[PAYLOAD_KEY_LJQS].append(row)
    return out
