"""三来源候选构建：初筛 → 合并去重 → 问财 + enrich → 拆回各来源。"""

from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.utils.dfcf_util import pkyd, ztgc
from app.utils.ths_util import hot_stock
from quant.pool.candidate_config import (
    PAYLOAD_KEY_PKYD,
    PAYLOAD_KEY_POPULARITY,
    PAYLOAD_KEY_ZT,
    SOURCE_LABEL_PKYD,
    SOURCE_LABEL_POPULARITY,
    SOURCE_LABEL_ZT,
    load_candidate_config,
    pkyd_labels,
    popularity_limit,
)
from quant.pool.pipeline import run_candidate_pipeline
from quant.pool.source_merge import merge_prefiltered_sources, split_enriched_by_source
from quant.pool.sources import (
    merge_pkyd_from_batches,
    prefilter_popularity,
    prefilter_zt_pool,
)


async def _prefilter_popularity(settings: Settings, cfg: dict) -> list[dict]:
    limit = settings.quant_hot_list_limit() if settings.QUANT_TEST_PHASE else popularity_limit(cfg)
    raw = await hot_stock(settings, limit)
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


async def _prefilter_pkyd(settings: Settings, cfg: dict) -> list[dict]:
    del settings
    batches: list[tuple[str, list[dict]]] = []
    for label in pkyd_labels(cfg):
        batch = pkyd(label)
        batches.append((label, batch if isinstance(batch, list) else []))
    return merge_pkyd_from_batches(batches, cfg=cfg)


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


async def build_pkyd_candidates(
    settings: Settings,
    payload: dict[str, Any],
    *,
    include_pre_snapshot: bool = False,
) -> list[dict]:
    cfg = load_candidate_config()
    rows = await _prefilter_pkyd(settings, cfg)
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
) -> dict[str, list[dict]]:
    """串行初筛三来源 → 按代码合并 → 一次问财+enrich → 拆回 payload 键。"""
    cfg = load_candidate_config()
    pop_rows = await _prefilter_popularity(settings, cfg)
    zt_rows_f = await _prefilter_zt(settings, cfg, zt_rows=zt_rows)
    pkyd_rows = await _prefilter_pkyd(settings, cfg)

    merged_rows, source_orders = merge_prefiltered_sources(pop_rows, zt_rows_f, pkyd_rows)
    enriched = await run_candidate_pipeline(
        settings,
        merged_rows,
        payload,
        include_pre_snapshot=include_pre_snapshot,
    )
    by_source = split_enriched_by_source(enriched, source_orders)

    return {
        PAYLOAD_KEY_POPULARITY: by_source.get(SOURCE_LABEL_POPULARITY, []),
        PAYLOAD_KEY_ZT: by_source.get(SOURCE_LABEL_ZT, []),
        PAYLOAD_KEY_PKYD: by_source.get(SOURCE_LABEL_PKYD, []),
    }
