"""三来源初筛结果：按代码合并 / 拆分（问财 与 enrich 各只做一次）。"""

from __future__ import annotations

from common.utils.common_util import extract_stock_code


def _code(row: dict) -> str:
    return extract_stock_code(row)


def _merge_source_label(existing: dict, row: dict) -> None:
    label = str(row.get("候选来源") or "").strip()
    if not label:
        return
    prev = existing.get("候选来源")
    if not prev:
        existing["候选来源"] = label
        return
    if isinstance(prev, list):
        if label not in prev:
            prev.append(label)
        return
    if prev != label:
        existing["候选来源"] = [prev, label]


def merge_prefiltered_sources(
    *row_groups: list[dict],
) -> tuple[list[dict], dict[str, list[str]]]:
    """合并多来源初筛行，返回 (去重列表, 各来源代码顺序表)。"""
    merged: dict[str, dict] = {}
    source_orders: dict[str, list[str]] = {}

    for rows in row_groups:
        if not rows:
            continue
        source_key = str(rows[0].get("候选来源") or "unknown")
        order = source_orders.setdefault(source_key, [])
        seen_in_source: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = _code(row)
            if not code:
                continue
            if code not in seen_in_source:
                order.append(code)
                seen_in_source.add(code)
            if code in merged:
                _merge_source_label(merged[code], row)
            else:
                item = dict(row)
                item["股票代码"] = code
                item["代码"] = code
                merged[code] = item

    from common.testing.trim import truncate_list_for_test_phase

    merged_rows = truncate_list_for_test_phase(list(merged.values()))
    allowed = {_code(r) for r in merged_rows if _code(r)}
    if allowed and len(allowed) < len(merged):
        filtered_orders: dict[str, list[str]] = {}
        for source_key, codes in source_orders.items():
            filtered_orders[source_key] = [c for c in codes if c in allowed]
        source_orders = filtered_orders

    return merged_rows, source_orders


def split_enriched_by_source(
    enriched: list[dict],
    source_orders: dict[str, list[str]],
) -> dict[str, list[dict]]:
    """将 enrich 后的行按来源顺序拆回 payload 键对应结构。"""
    by_code = {_code(r): r for r in enriched if _code(r)}
    out: dict[str, list[dict]] = {}
    for source_key, codes in source_orders.items():
        out[source_key] = [by_code[c] for c in codes if c in by_code]
    return out
