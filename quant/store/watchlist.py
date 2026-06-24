"""自选股保留策略：末次入选后 N 个交易日内可保留，超期未再入选则移出。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from app.utils.common_util import is_real_workday_cn

if TYPE_CHECKING:
    from quant.scoring.context import ScoreContext
    from quant.scoring.engine import ScoringEngine


def _parse_iso(d: object) -> date | None:
    s = str(d or "").strip()[:10]
    if not s:
        return None
    try:
        return date.fromisoformat(s.replace("/", "-"))
    except ValueError:
        return None


def count_workdays_between(start: date, end: date, *, exclusive_start: bool = True) -> int:
    """统计 [start, end] 区间内的工作日数；默认不含 start（用于「末次入选之后过了几天」）。"""
    if end < start:
        return 0
    cur = start + timedelta(days=1) if exclusive_start else start
    n = 0
    while cur <= end:
        if is_real_workday_cn(cur):
            n += 1
        cur += timedelta(days=1)
    return n


def watchlist_retain_days(cfg: dict | None = None) -> int:
    from quant.config import load_scoring_config

    c = (load_scoring_config().get("candidate") or {}) if cfg is None else cfg
    return max(1, int(c.get("watchlist_retain_days", 5)))


def should_drop_watchlist_row(row: dict, *, today: date | None = None, retain_days: int | None = None) -> bool:
    """末次入选后连续 retain_days 个交易日未再入选 → 应移出。"""
    retain = retain_days if retain_days is not None else watchlist_retain_days()
    ref = today or datetime.now().date()
    last = _parse_iso(row.get("最后入选日期") or row.get("加入日期"))
    if last is None:
        return False
    gap = count_workdays_between(last, ref, exclusive_start=True)
    return gap >= retain


def merge_watchlist_evening(
    existing: list[dict],
    passed_rows: list[dict],
    *,
    today: date | None = None,
    retain_days: int | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """合并晚间达标标的与历史自选（滚动保留）。

    返回 (merged, added, removed)。
    """
    ref = today or datetime.now().date()
    retain = retain_days if retain_days is not None else watchlist_retain_days()
    today_s = ref.isoformat()

    passed_by_code = {str(r.get("股票代码", "")).strip(): r for r in passed_rows}
    passed_codes = set(passed_by_code)

    merged: list[dict] = []
    removed: list[dict] = []
    old_codes = set()

    for row in existing:
        code = str(row.get("股票代码", "")).strip()
        if not code:
            continue
        old_codes.add(code)
        if code in passed_codes:
            continue
        if should_drop_watchlist_row(row, today=ref, retain_days=retain):
            removed.append(dict(row))
        else:
            kept = dict(row)
            if not kept.get("最后入选日期"):
                kept["最后入选日期"] = ref.isoformat()
            merged.append(kept)

    added: list[dict] = []
    for code, row in passed_by_code.items():
        if not code:
            continue
        new_row = {**row, "最后入选日期": today_s}
        if code not in old_codes:
            added.append(new_row)
        merged.append(new_row)

    merged.sort(key=lambda r: (-float(r.get("评分", 0) or 0), str(r.get("股票代码", ""))))
    return merged, added, removed


def index_enriched_watchlist(payload: dict) -> dict[str, dict]:
    """payload「自选股」按代码索引（晚间 enrich 后的行情/资金流等）。"""
    out: dict[str, dict] = {}
    for row in payload.get("自选股") or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("股票代码", "")).strip()
        if code:
            out[code] = row
    return out


def supplement_retained_watchlist_scores(
    ctx: ScoreContext,
    engine: ScoringEngine,
    merged: list[dict],
    *,
    scores: list,
    score_by_code: dict[str, object],
    enriched_by_code: dict[str, dict],
) -> int:
    """保留自选若未进当晚候选池，仍用 enrich 数据补算当日评分。返回补算只数。"""
    n = 0
    for row in merged:
        code = str(row.get("股票代码", "")).strip()
        if not code or code in score_by_code:
            continue
        enriched = enriched_by_code.get(code)
        if not enriched:
            continue
        scored = engine.score_stock(ctx, enriched)
        score_by_code[code] = scored
        scores.append(scored)
        n += 1
    return n
