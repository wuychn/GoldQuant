"""晚间自选池更新（无 LLM/落盘，供 orchestrator 与回测共用）。"""

from __future__ import annotations

from dataclasses import dataclass

from app.utils.common_util import extract_stock_code

from quant.constants import STRATEGY_NAME
from quant.narrative.stock_lines import build_watchlist_human_reason, refresh_merged_watchlist_reasons
from quant.pool.builder import build_candidates
from quant.pool.ths_rank_util import stock_ths_rank_tags
from quant.scoring.context import ScoreContext
from quant.scoring.selection import SelectionScorer
from quant.store.observe_pool import (
    supplement_observe_scores,
    update_observe_pool_evening,
    watchlist_observe_max_days,
)
from quant.store.watchlist import (
    apply_watchlist_fail_streak,
    index_enriched_watchlist,
    merge_watchlist_evening,
    supplement_retained_watchlist_scores,
    watchlist_fail_streak_limit,
)


def _score_in_main_wave(score) -> bool:
    for d in getattr(score, "dimensions", []) or []:
        if getattr(d, "name", "") == "main_wave":
            return bool(d.detail.get("主升波段"))
    return False


@dataclass
class EveningWatchlistResult:
    watchlist: list[dict]
    observe_pool: list[dict]
    added: list[dict]
    moved_to_observe: list[dict]
    restored: list[dict]
    purged: list[dict]
    scores: list


def update_watchlist_evening_core(
    ctx: ScoreContext,
    *,
    existing_watchlist: list[dict],
    existing_observe: list[dict],
    engine: SelectionScorer | None = None,
) -> EveningWatchlistResult:
    """晚间复盘核心：候选评分 → 自选 hysteresis → 观察池。"""
    engine = engine or SelectionScorer()
    fail_limit = watchlist_fail_streak_limit()
    observe_limit = watchlist_observe_max_days()

    candidates = build_candidates(ctx.payload)
    by_code = {str(c.get("股票代码", "")).strip(): c for c in candidates}
    scores = engine.score_for_watchlist(ctx, candidates)
    passed = [s for s in scores if s.passed_threshold]

    require_main_wave = bool(engine.config.get("watchlist_require_main_wave", False))
    if require_main_wave:
        passed = [s for s in passed if _score_in_main_wave(s)]
    passed.sort(key=lambda x: x.total, reverse=True)

    passed_rows: list[dict] = []
    for s in passed:
        cand = by_code.get(s.code, {})
        row = {
            "股票代码": s.code,
            "股票名称": s.name,
            "战法": STRATEGY_NAME,
            "评分": round(s.total, 2),
            "加入自选原因": build_watchlist_human_reason(s, cand),
        }
        ths_tags = stock_ths_rank_tags(cand)
        if ths_tags:
            row["榜单标签"] = ths_tags
        passed_rows.append(row)

    pre_existing_codes = {
        extract_stock_code(r) for r in existing_watchlist if extract_stock_code(r)
    }
    merged, added, _ = merge_watchlist_evening(existing_watchlist, passed_rows)
    score_by_code = {s.code: s for s in scores}
    enriched_by_code = index_enriched_watchlist(ctx.payload)

    supplement_observe_scores(
        ctx,
        engine,
        existing_observe,
        scores=scores,
        score_by_code=score_by_code,
        enriched_by_code=enriched_by_code,
    )
    supplement_retained_watchlist_scores(
        ctx,
        engine,
        merged,
        scores=scores,
        score_by_code=score_by_code,
        enriched_by_code=enriched_by_code,
    )

    base_threshold = float(engine.config.get("watchlist_threshold", 70))
    exit_threshold = float(engine.config.get("watchlist_exit_threshold", base_threshold))
    entry_threshold = float(engine.config.get("watchlist_entry_threshold", base_threshold))

    merged, to_observe = apply_watchlist_fail_streak(
        merged,
        score_by_code=score_by_code,
        threshold=exit_threshold,
        max_streak=fail_limit,
    )

    observe_input = existing_observe + to_observe
    remaining_observe, restored, purged = update_observe_pool_evening(
        observe_input,
        ctx=ctx,
        engine=engine,
        score_by_code=score_by_code,
        enriched_by_code=enriched_by_code,
        threshold=entry_threshold,
        max_days=observe_limit,
    )

    merged_codes = {extract_stock_code(r) for r in merged if extract_stock_code(r)}
    for r in restored:
        c = extract_stock_code(r)
        if c and c not in merged_codes:
            merged_codes.add(c)
            merged.append(r)

    candidate_by_code = {**enriched_by_code, **by_code}
    refresh_merged_watchlist_reasons(
        merged,
        score_by_code=score_by_code,
        candidate_by_code=candidate_by_code,
    )
    merged.sort(
        key=lambda r: (
            -float(r.get("评分") or 0),
            -float(r.get("动能分") or 0),
            str(r.get("股票代码", "")),
        )
    )

    return EveningWatchlistResult(
        watchlist=merged,
        observe_pool=remaining_observe,
        added=added,
        moved_to_observe=to_observe,
        restored=restored,
        purged=purged,
        scores=scores,
    )
