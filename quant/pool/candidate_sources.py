"""三来源候选构建：初筛 → 合并去重 → 问财 + enrich → 拆回各来源。"""

from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.utils.dfcf_util import ztgc
from app.utils.ths_rank_fetch import fetch_with_retry
from app.utils.ths_util import cxfl, cxg, hot_stock, ljqs, lxsz
from quant.pool.candidate_config import (
    PAYLOAD_KEY_CXFL,
    PAYLOAD_KEY_CXG,
    PAYLOAD_KEY_LJQS,
    PAYLOAD_KEY_LXSZ,
    PAYLOAD_KEY_POPULARITY,
    PAYLOAD_KEY_ZT,
    SOURCE_LABEL_THS_RANK,
    SOURCE_LABEL_POPULARITY,
    SOURCE_LABEL_ZT,
    cxg_labels,
    load_candidate_config,
)
from quant.pool.pipeline import run_candidate_pipeline
from quant.pool.source_merge import merge_prefiltered_sources, split_enriched_by_source
from quant.pool.sources import (
    merge_ths_rank_from_batches,
    prefilter_popularity,
    prefilter_ths_rank,
    prefilter_zt_pool,
)
from quant.pool.ths_rank_util import split_enriched_ths_rank_payload
from quant.progress_log import log_progress, log_progress_done


async def _prefilter_popularity(settings: Settings, cfg: dict) -> list[dict]:
    from quant.pool.candidate_config import popularity_limit

    limit = settings.quant_hot_list_limit() if settings.QUANT_TEST_PHASE else popularity_limit(cfg)
    raw = await hot_stock(limit)
    return prefilter_popularity(raw if isinstance(raw, list) else [], cfg=cfg)


async def _prefilter_zt(
    settings: Settings,
    cfg: dict,
    *,
    zt_rows: list[dict] | None = None,
) -> list[dict]:
    del settings
    raw = zt_rows if zt_rows is not None else ztgc(filter_first=False)
    return prefilter_zt_pool(raw if isinstance(raw, list) else [], cfg=cfg)


async def _prefilter_ths_rank(
    settings: Settings,
    cfg: dict,
    *,
    progress_scope: str = "candidates",
) -> list[dict]:
    del settings
    batches: list[tuple[str, list[dict]]] = []

    async def _fetch_batch(label: str, fetch_fn) -> list[dict]:
        rows = await fetch_with_retry(
            label,
            fetch_fn,
            default=[],
            progress_scope=progress_scope,
        )
        return rows if isinstance(rows, list) else []

    for label in cxg_labels(cfg):
        batches.append((label, await _fetch_batch(label, lambda l=label: cxg(l))))
    for label, fn in (
        ("持续上涨", lxsz),
        ("持续放量", cxfl),
        ("量价齐升", ljqs),
    ):
        batches.append((label, await _fetch_batch(label, fn)))
    return merge_ths_rank_from_batches(batches, cfg=cfg)


async def build_ths_rank_candidates(
    settings: Settings,
    payload: dict[str, Any],
    *,
    include_pre_snapshot: bool = False,
) -> dict[str, list[dict]]:
    cfg = load_candidate_config()
    rows = await _prefilter_ths_rank(settings, cfg)
    enriched = await run_candidate_pipeline(
        settings,
        rows,
        payload,
        include_pre_snapshot=include_pre_snapshot,
    )
    return split_enriched_ths_rank_payload(enriched)


async def build_popularity_candidates(
    settings: Settings,
    payload: dict[str, Any],
    *,
    include_pre_snapshot: bool = False,
) -> list[dict]:
    cfg = load_candidate_config()
    rows = await _prefilter_popularity(settings, cfg)
    return await run_candidate_pipeline(
        settings,
        rows,
        payload,
        include_pre_snapshot=include_pre_snapshot,
    )


async def build_zt_candidates(
    settings: Settings,
    payload: dict[str, Any],
    *,
    zt_rows: list[dict] | None = None,
    include_pre_snapshot: bool = False,
) -> list[dict]:
    cfg = load_candidate_config()
    rows = await _prefilter_zt(settings, cfg, zt_rows=zt_rows)
    return await run_candidate_pipeline(
        settings,
        rows,
        payload,
        include_pre_snapshot=include_pre_snapshot,
    )


async def build_all_source_candidates(
    settings: Settings,
    payload: dict[str, Any],
    *,
    zt_rows: list[dict] | None = None,
    include_pre_snapshot: bool = False,
    progress_scope: str = "candidates",
) -> dict[str, list[dict]]:
    """串行初筛三来源 → 按代码合并 → 一次问财+enrich → 拆回 payload 键。"""
    cfg = load_candidate_config()
    log_progress(progress_scope, "初筛：人气榜")
    pop_rows = await fetch_with_retry(
        "人气榜",
        lambda: _prefilter_popularity(settings, cfg),
        default=[],
        progress_scope=progress_scope,
    )
    log_progress(progress_scope, "初筛：涨停池")
    zt_rows_f = await fetch_with_retry(
        "涨停池",
        lambda: _prefilter_zt(settings, cfg, zt_rows=zt_rows),
        default=[],
        progress_scope=progress_scope,
    )
    log_progress(progress_scope, "初筛：同花顺形态榜")
    ths_rows = await _prefilter_ths_rank(settings, cfg, progress_scope=progress_scope)
    log_progress(
        progress_scope,
        "初筛完成",
        detail=f"人气 {len(pop_rows)} / 涨停 {len(zt_rows_f)} / 形态 {len(ths_rows)}",
    )

    merged_rows, source_orders = merge_prefiltered_sources(pop_rows, zt_rows_f, ths_rows)
    log_progress(progress_scope, "合并去重", detail=f"unique {len(merged_rows)} 只")
    enriched = await run_candidate_pipeline(
        settings,
        merged_rows,
        payload,
        include_pre_snapshot=include_pre_snapshot,
        progress_scope=progress_scope,
    )
    by_source = split_enriched_by_source(enriched, source_orders)
    ths_enriched = by_source.get(SOURCE_LABEL_THS_RANK, [])
    ths_payload = split_enriched_ths_rank_payload(ths_enriched)
    log_progress(
        progress_scope,
        "形态榜拆分",
        detail=(
            f"创新高 {len(ths_payload[PAYLOAD_KEY_CXG])} / "
            f"持续上涨 {len(ths_payload[PAYLOAD_KEY_LXSZ])} / "
            f"持续放量 {len(ths_payload[PAYLOAD_KEY_CXFL])} / "
            f"量价齐升 {len(ths_payload[PAYLOAD_KEY_LJQS])}"
        ),
    )
    result = {
        PAYLOAD_KEY_POPULARITY: by_source.get(SOURCE_LABEL_POPULARITY, []),
        PAYLOAD_KEY_ZT: by_source.get(SOURCE_LABEL_ZT, []),
        **ths_payload,
    }
    log_progress_done(
        progress_scope,
        "三来源候选就绪",
        detail=(
            f"人气 {len(result[PAYLOAD_KEY_POPULARITY])} / "
            f"涨停 {len(result[PAYLOAD_KEY_ZT])} / "
            f"形态 {len(ths_enriched)}"
        ),
    )
    return result
