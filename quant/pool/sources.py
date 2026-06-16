"""三来源初筛：各来源只负责「拉 raw + 初筛」，不关心 enrich / 概念 / 评分。"""

from __future__ import annotations

from quant.pool.candidate_config import (
    SOURCE_LABEL_PKYD,
    SOURCE_LABEL_POPULARITY,
    SOURCE_LABEL_ZT,
    load_candidate_config,
    pkyd_dual_tag_limit,
    pkyd_labels,
    pkyd_min_tags,
    popularity_limit,
    zt_min_boards,
)
from quant.pool.pkyd_filter import passes_pkyd_main_wave_filter
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
    """盘口异动初筛：仅标的池（沪主/深主/创业板，剔除 ST、科创板、北交所等）。

    技术形态（近3月涨幅、本月阳线、日/周/月主升）在 enrich 后由 ``postfilter_pkyd_acceleration`` 统一筛选。
    """
    min_tags = pkyd_min_tags(cfg)
    filtered = apply_symbol_pool_filter(rows)
    out: list[dict] = []
    for row in filtered:
        if min_tags > 0 and _pkyd_tag_count(row) < min_tags:
            continue
        norm = normalize_stock_row(row, source=SOURCE_LABEL_PKYD)
        if norm:
            out.append(norm)
    out.sort(key=lambda r: str(r.get("股票代码", "")))
    return out


def postfilter_pkyd_acceleration(rows: list[dict], *, cfg: dict | None = None) -> list[dict]:
    """enrich 后：近3月涨幅+本月阳线+日/周/月主升，再按近3月涨幅取前 N。"""
    from quant.config import load_gates_config
    from quant.scoring.tech_indicators import monthly_three_month_return_pct

    c = cfg or load_candidate_config()
    mw = load_gates_config().get("main_wave") or {}
    limit = pkyd_dual_tag_limit(c)

    passed: list[dict] = []
    for row in rows:
        ok, _, _ = passes_pkyd_main_wave_filter(row, mw_cfg=mw, candidate_cfg=c)
        if ok:
            passed.append(row)
    passed.sort(
        key=lambda r: monthly_three_month_return_pct(r.get("历史行情") or []) or -1e9,
        reverse=True,
    )
    return passed[:limit]


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
