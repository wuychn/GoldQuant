"""自选股保留策略：连续评分不达标 N 日后移入观察池。"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from app.utils.common_util import extract_stock_code

if TYPE_CHECKING:
    from quant.scoring.context import ScoreContext
    from quant.scoring.engine import ScoringEngine


def watchlist_fail_streak_limit(cfg: dict | None = None) -> int:
    """连续评分不达标多少个交易日移入观察池（配置键 watchlist_retain_days）。"""
    from quant.config import load_scoring_config

    c = (load_scoring_config().get("candidate") or {}) if cfg is None else cfg
    return max(1, int(c.get("watchlist_retain_days", 3)))


# 兼容旧调用
watchlist_retain_days = watchlist_fail_streak_limit


def _fail_streak(row: dict) -> int:
    try:
        return max(0, int(row.get("未达标连续天数") or 0))
    except (TypeError, ValueError):
        return 0


def _normalize_row_code(row: dict) -> str:
    return extract_stock_code(row)


def merge_watchlist_evening(
    existing: list[dict],
    passed_rows: list[dict],
    *,
    today: date | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """合并晚间达标标的与历史自选（移入观察池由 ``apply_watchlist_fail_streak`` 处理）。

    返回 (merged, added, removed)；removed 恒为空列表。
    ``added`` 仅含此前不在自选中的代码（归一化后比对，避免重复计为「新入选」）。
    """
    ref = today or datetime.now().date()
    today_s = ref.isoformat()

    passed_by_code: dict[str, dict] = {}
    for row in passed_rows:
        code = _normalize_row_code(row)
        if code:
            passed_by_code[code] = {**row, "股票代码": code}
    passed_codes = set(passed_by_code)

    pre_existing_codes: set[str] = set()
    for row in existing:
        code = _normalize_row_code(row)
        if code:
            pre_existing_codes.add(code)

    merged: list[dict] = []
    for row in existing:
        code = _normalize_row_code(row)
        if not code:
            continue
        if code in passed_codes:
            continue
        kept = dict(row)
        kept["股票代码"] = code
        if not kept.get("最后入选日期"):
            kept["最后入选日期"] = ref.isoformat()
        merged.append(kept)

    added: list[dict] = []
    for code, row in passed_by_code.items():
        new_row = {**row, "最后入选日期": today_s, "未达标连续天数": 0}
        if code not in pre_existing_codes:
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
    """按当日评分更新连续未达标天数；达 ``max_streak`` 则移入观察池。返回 (kept, to_observe)。"""
    from quant.store.observe_pool import row_to_observe

    limit = max_streak if max_streak is not None else watchlist_fail_streak_limit()
    kept: list[dict] = []
    to_observe: list[dict] = []
    for row in merged:
        code = _normalize_row_code(row)
        score_obj = score_by_code.get(code)
        item = dict(row)
        item["股票代码"] = code
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
            to_observe.append(row_to_observe(item))
        else:
            kept.append(item)
    kept.sort(
        key=lambda r: (
            -float(r.get("动能分") or 0),
            -float(r.get("评分") or 0),
            str(r.get("股票代码", "")),
        )
    )
    return kept, to_observe


def index_enriched_watchlist(payload: dict) -> dict[str, dict]:
    """payload「自选股」与内部观察 enrich 按代码索引。"""
    out: dict[str, dict] = {}
    for key in ("自选股", "_observe_enriched"):
        for row in payload.get(key) or []:
            if not isinstance(row, dict):
                continue
            code = _normalize_row_code(row)
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
        code = _normalize_row_code(row)
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
