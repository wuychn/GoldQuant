"""自选/持仓 enrich（service 层，避免 tools 依赖 app.services）。"""

from __future__ import annotations

from typing import Any

from fastapi.concurrency import run_in_threadpool

from quant.data.tools.market import async_holding_rows, log_tool_error
from quant.progress_log import log_progress
from quant.store.state import merge_holding_meta


async def enrich_optional_and_holding_from_rows(
    settings: Any,
    optional: list,
    holding: list,
    *,
    progress_scope: str | None = None,
    include_pre_snapshot: bool = True,
    skip_wencai: bool = False,
    skip_jbxx: bool = False,
    extra_rows: list | None = None,
) -> tuple[list, list, list]:
    from app.services.stock_enrich import enrich_stock_rows
    from quant.testing.trim import truncate_list_for_test_phase

    optional = optional if isinstance(optional, list) else []
    holding = holding if isinstance(holding, list) else []
    optional = truncate_list_for_test_phase(optional, settings)
    holding = truncate_list_for_test_phase(holding, settings)

    def _code(row: dict) -> str:
        return str(row.get("股票代码", "")).strip()

    seen: set[str] = set()
    unique_rows: list[dict] = []
    for row in optional:
        if not isinstance(row, dict):
            continue
        c = _code(row)
        if not c or c in seen:
            continue
        seen.add(c)
        unique_rows.append(dict(row))
    for row in holding:
        if not isinstance(row, dict):
            continue
        c = _code(row)
        if not c or c in seen:
            continue
        seen.add(c)
        unique_rows.append(dict(row))
    for row in extra_rows or []:
        if not isinstance(row, dict):
            continue
        c = _code(row)
        if not c or c in seen:
            continue
        seen.add(c)
        unique_rows.append(dict(row))

    unique_rows = truncate_list_for_test_phase(unique_rows, settings)

    if progress_scope:
        log_progress(
            progress_scope,
            "enrich 自选/持仓",
            detail=f"自选 {len(optional)} + 持仓 {len(holding)} → 去重 {len(unique_rows)}",
        )

    enriched = await enrich_stock_rows(
        settings,
        unique_rows,
        include_pre_snapshot=include_pre_snapshot,
        skip_wencai=skip_wencai,
        skip_jbxx=skip_jbxx,
        progress_scope=progress_scope,
        progress_label="enrich",
    )
    by_code = {_code(r): r for r in enriched if isinstance(r, dict) and _code(r)}

    zxg = [by_code[c] for r in optional if isinstance(r, dict) and (c := _code(r)) in by_code]
    ccg = [
        merge_holding_meta(by_code[c], r)
        for r in holding
        if isinstance(r, dict) and (c := _code(r)) in by_code
    ]
    observe_out = [
        by_code[c]
        for r in (extra_rows or [])
        if isinstance(r, dict) and (c := _code(r)) in by_code
    ]

    if progress_scope:
        log_progress(
            progress_scope,
            "自选/持仓 enrich 完成",
            detail=f"自选 {len(zxg)} 只，持仓 {len(ccg)} 只，观察 {len(observe_out)} 只",
        )
    return zxg, ccg, observe_out


async def enrich_optional_and_holding(
    settings: Any,
    *,
    progress_scope: str | None = None,
    include_pre_snapshot: bool = True,
    skip_wencai: bool = False,
    skip_jbxx: bool = False,
) -> tuple[list, list]:
    holding = await async_holding_rows()
    zxg_, ccg_, _ = await enrich_optional_and_holding_from_rows(
        settings,
        [],
        holding if isinstance(holding, list) else [],
        progress_scope=progress_scope,
        include_pre_snapshot=include_pre_snapshot,
        skip_wencai=skip_wencai,
        skip_jbxx=skip_jbxx,
    )
    return zxg_, ccg_
