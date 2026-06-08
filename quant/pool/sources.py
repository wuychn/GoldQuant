"""三来源初筛：各来源只负责「拉 raw + 初筛」，不关心 enrich / 概念 / 评分。"""

from __future__ import annotations

from quant.pool.candidate_config import (
    SOURCE_LABEL_PKYD,
    SOURCE_LABEL_POPULARITY,
    SOURCE_LABEL_ZT,
    load_candidate_config,
    pkyd_dual_tag_limit,
    pkyd_labels,
    popularity_limit,
    zt_min_boards,
)
from quant.pool.pkyd_util import extract_pkyd_code, merge_pkyd_rows_by_code
from quant.pool.symbol_filter import apply_symbol_pool_filter, normalize_stock_row


def _board_count(row: dict) -> int:
    try:
        return int(float(row.get("连板数", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _pkyd_tag_count(row: dict) -> int:
    tags = row.get("异动类型") or row.get("盘口异动标签") or row.get("原因") or []
    if isinstance(tags, str):
        tags = [tags] if tags.strip() else []
    return len([t for t in tags if str(t).strip()])


def prefilter_popularity(rows: list[dict] | None, *, cfg: dict | None = None) -> list[dict]:
    """人气榜：标的池 + 非 ST → 取前 N。"""
    limit = popularity_limit(cfg)
    filtered = apply_symbol_pool_filter(rows)
    out: list[dict] = []
    for row in filtered[:limit]:
        norm = normalize_stock_row(row, source=SOURCE_LABEL_POPULARITY)
        if norm:
            out.append(norm)
    return out


def prefilter_zt_pool(rows: list[dict] | None, *, cfg: dict | None = None) -> list[dict]:
    """涨停池：标的池 + 非 ST → 连板数 ≥ 配置下限。"""
    min_boards = zt_min_boards(cfg)
    filtered = apply_symbol_pool_filter(rows)
    out: list[dict] = []
    for row in filtered:
        if _board_count(row) < min_boards:
            continue
        norm = normalize_stock_row(row, source=SOURCE_LABEL_ZT)
        if norm:
            out.append(norm)
    return out


def prefilter_pkyd(rows: list[dict] | None, *, cfg: dict | None = None) -> list[dict]:
    """盘口异动：标的池 + 非 ST → 双标签优先取前 N。"""
    limit = pkyd_dual_tag_limit(cfg)
    filtered = apply_symbol_pool_filter(rows)
    dual: list[dict] = []
    for row in filtered:
        if _pkyd_tag_count(row) < 2:
            continue
        norm = normalize_stock_row(row, source=SOURCE_LABEL_PKYD)
        if norm:
            dual.append(norm)
    dual.sort(key=lambda r: (-_pkyd_tag_count(r), str(r.get("股票代码", ""))))
    return dual[:limit]


def merge_pkyd_from_batches(
    batches: list[tuple[str, list[dict]]],
    *,
    cfg: dict | None = None,
) -> list[dict]:
    """东财多批次异动 raw 行 → 按代码合并后走 ``prefilter_pkyd``。"""
    c = cfg or load_candidate_config()
    labels = set(pkyd_labels(c))
    entries: list[tuple[str, str | None, str]] = []
    for label, batch in batches:
        if label not in labels:
            continue
        for row in batch or []:
            if not isinstance(row, dict):
                continue
            code = extract_pkyd_code(row)
            if not code:
                continue
            entries.append((code, row.get("名称") or row.get("股票名称"), label))
    merged = merge_pkyd_rows_by_code(entries=entries)
    return prefilter_pkyd(merged, cfg=c)
