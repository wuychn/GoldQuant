"""自选股保留策略：连续评分不达标 N 个交易日后移出。"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from quant.scoring.context import ScoreContext
    from quant.scoring.engine import ScoringEngine


def watchlist_fail_streak_limit(cfg: dict | None = None) -> int:
    """连续评分不达标多少个交易日移出自选（配置键 watchlist_retain_days）。"""
    from quant.config import load_scoring_config

    c = (load_scoring_config().get("candidate") or {}) if cfg is None else cfg
    return max(1, int(c.get("watchlist_retain_days", 5)))


# 兼容旧调用
watchlist_retain_days = watchlist_fail_streak_limit


def _fail_streak(row: dict) -> int:
    try:
        return max(0, int(row.get("未达标连续天数") or 0))
    except (TypeError, ValueError):
        return 0


def merge_watchlist_evening(
    existing: list[dict],
    passed_rows: list[dict],
    *,
    today: date | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """合并晚间达标标的与历史自选（移出由 ``apply_watchlist_fail_streak`` 处理）。

    返回 (merged, added, removed)；removed 恒为空列表。
    """
    ref = today or datetime.now().date()
    today_s = ref.isoformat()

    passed_by_code = {str(r.get("股票代码", "")).strip(): r for r in passed_rows}
    passed_codes = set(passed_by_code)

    merged: list[dict] = []
    old_codes: set[str] = set()

    for row in existing:
        code = str(row.get("股票代码", "")).strip()
        if not code:
            continue
        old_codes.add(code)
        if code in passed_codes:
            continue
        kept = dict(row)
        if not kept.get("最后入选日期"):
            kept["最后入选日期"] = ref.isoformat()
        merged.append(kept)

    added: list[dict] = []
    for code, row in passed_by_code.items():
        if not code:
            continue
        new_row = {**row, "最后入选日期": today_s, "未达标连续天数": 0}
        if code not in old_codes:
            added.append(new_row)
        merged.append(new_row)

    merged.sort(key=lambda r: (-float(r.get("评分", 0) or 0), str(r.get("股票代码", ""))))
    return merged, added, []


def apply_watchlist_fail_streak(
    merged: list[dict],
    *,
    score_by_code: dict[str, Any],
    threshold: float,
    max_streak: int | None = None,
) -> tuple[list[dict], list[dict]]:
    """按当日评分更新连续未达标天数；达 ``max_streak`` 则移出。返回 (kept, removed)。"""
    limit = max_streak if max_streak is not None else watchlist_fail_streak_limit()
    kept: list[dict] = []
    removed: list[dict] = []
    for row in merged:
        code = str(row.get("股票代码", "")).strip()
        score_obj = score_by_code.get(code)
        item = dict(row)
        if score_obj is None:
            kept.append(item)
            continue
        total = float(getattr(score_obj, "total", 0) or 0)
        if total >= threshold:
            item["未达标连续天数"] = 0
            kept.append(item)
            continue
        streak = _fail_streak(item) + 1
        item["未达标连续天数"] = streak
        if streak >= limit:
            removed.append(item)
        else:
            kept.append(item)
    kept.sort(key=lambda r: (-float(r.get("评分", 0) or 0), str(r.get("股票代码", ""))))
    return kept, removed


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
