"""通用候选股漏斗：问财补概念 → enrich（概念不参与硬过滤，由评分维度处理）。"""

from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.services.stock_enrich import attach_concepts_to_rows, enrich_stock_rows
from quant.progress_log import log_progress, log_progress_done


async def run_candidate_pipeline(
    settings: Settings,
    rows: list[dict],
    payload: dict[str, Any] | None = None,
    *,
    include_pre_snapshot: bool = False,
    progress_scope: str = "candidate_pipeline",
) -> list[dict]:
    """初筛后的列表 → 串行问财补概念 → 串行 enrich（不再做概念硬过滤）。"""
    del payload
    if not rows:
        log_progress(progress_scope, "候选为空，跳过问财/enrich")
        return []
    log_progress(progress_scope, "问财补概念", detail=f"共 {len(rows)} 只")
    with_concepts = await attach_concepts_to_rows(
        rows,
        progress_scope=progress_scope,
        progress_label="问财",
    )
    log_progress(progress_scope, "enrich 行情数据", detail=f"共 {len(with_concepts)} 只")
    enriched = await enrich_stock_rows(
        settings,
        with_concepts,
        include_pre_snapshot=include_pre_snapshot,
        skip_wencai=True,
        progress_scope=progress_scope,
        progress_label="enrich",
    )
    log_progress_done(progress_scope, "问财+enrich 完成", detail=f"{len(enriched)} 只")
    return enriched
