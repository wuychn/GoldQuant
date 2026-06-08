"""通用候选股漏斗：问财补概念 → enrich（概念不参与硬过滤，由评分维度处理）。"""

from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.services.stock_enrich import attach_concepts_to_rows, enrich_stock_rows


async def run_candidate_pipeline(
    settings: Settings,
    rows: list[dict],
    payload: dict[str, Any] | None = None,
    *,
    include_pre_snapshot: bool = False,
) -> list[dict]:
    """初筛后的列表 → 串行问财补概念 → 串行 enrich（不再做概念硬过滤）。"""
    del payload  # 保留参数以兼容旧调用；概念强弱由 scoring.concept_theme 处理
    if not rows:
        return []
    with_concepts = await attach_concepts_to_rows(rows)
    return await enrich_stock_rows(
        settings,
        with_concepts,
        include_pre_snapshot=include_pre_snapshot,
        skip_wencai=True,
    )
