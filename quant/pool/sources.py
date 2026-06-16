"""三来源初筛：各来源只负责「拉 raw + 初筛」，不关心 enrich / 概念 / 评分。"""

from __future__ import annotations

from quant.pool.candidate_config import (
    SOURCE_LABEL_POPULARITY,
    SOURCE_LABEL_THS_RANK,
    SOURCE_LABEL_ZT,
    cxg_labels,
    load_candidate_config,
    popularity_limit,
    zt_min_boards,
)
from quant.pool.ths_rank_util import extract_ths_rank_code, merge_ths_rank_rows_by_code
from quant.pool.symbol_filter import apply_symbol_pool_filter, normalize_stock_row


def _board_count(row: dict) -> int:
    try:
        return int(float(row.get("连板数", 0) or 0))
    except (TypeError, ValueError):
        return 0


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


def prefilter_ths_rank(rows: list[dict] | None, *, cfg: dict | None = None) -> list[dict]:
    """同花顺形态榜初筛：标的池（沪主/深主/创业板，剔除 ST、科创板、北交所等）。"""
    del cfg
    filtered = apply_symbol_pool_filter(rows)
    out: list[dict] = []
    for row in filtered:
        norm = normalize_stock_row(row, source=SOURCE_LABEL_THS_RANK)
        if norm:
            out.append(norm)
    out.sort(key=lambda r: str(r.get("股票代码", "")))
    return out


def merge_ths_rank_from_batches(
    batches: list[tuple[str, list[dict]]],
    *,
    cfg: dict | None = None,
) -> list[dict]:
    """同花顺多批次形态榜 raw 行 → 按代码合并后走 ``prefilter_ths_rank``。"""
    c = cfg or load_candidate_config()
    cxg_set = set(cxg_labels(c))
    entries: list[tuple[str, str | None, str]] = []
    for label, batch in batches:
        if label in cxg_set or label in ("持续上涨", "持续放量", "量价齐升"):
            tag = label
        else:
            continue
        for row in batch or []:
            if not isinstance(row, dict):
                continue
            code = extract_ths_rank_code(row)
            if not code:
                continue
            name = row.get("股票简称") or row.get("股票名称") or row.get("名称")
            entries.append((code, name, tag))
    merged = merge_ths_rank_rows_by_code(entries=entries)
    return prefilter_ths_rank(merged, cfg=c)
