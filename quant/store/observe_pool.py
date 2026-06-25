"""观察池：自选连续不达标后暂存，晚间复盘仍评分，不对外返回。"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from app.utils.common_util import extract_stock_code

if TYPE_CHECKING:
    from quant.scoring.context import ScoreContext
    from quant.scoring.engine import ScoringEngine


def watchlist_observe_max_days(cfg: dict | None = None) -> int:
    from quant.config import load_scoring_config

    c = (load_scoring_config().get("candidate") or {}) if cfg is None else cfg
    return max(1, int(c.get("watchlist_observe_max_days", 30)))


def _observe_days(row: dict) -> int:
    try:
        return max(0, int(row.get("观察天数") or 0))
    except (TypeError, ValueError):
        return 0


def row_to_observe(row: dict, *, today: date | None = None) -> dict:
    ref = today or datetime.now().date()
    code = extract_stock_code(row)
    item = dict(row)
    item["股票代码"] = code
    item["未达标连续天数"] = 0
    item["观察天数"] = 0
    item["进入观察池日期"] = ref.isoformat()
    item["最后观察日期"] = ref.isoformat()
    return item


def supplement_observe_scores(
    ctx: ScoreContext,
    engine: ScoringEngine,
    rows: list[dict],
    *,
    scores: list,
    score_by_code: dict[str, object],
    enriched_by_code: dict[str, dict],
) -> int:
    """观察池未进候选池时，用 enrich 数据补算评分。"""
    n = 0
    for row in rows:
        code = extract_stock_code(row)
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


def update_observe_pool_evening(
    rows: list[dict],
    *,
    ctx: ScoreContext,
    engine: ScoringEngine,
    score_by_code: dict[str, Any],
    enriched_by_code: dict[str, dict],
    threshold: float,
    max_days: int | None = None,
    today: date | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """观察池 nightly 更新：达标恢复自选，否则观察天数+1，超限彻底删除。

    返回 (remaining_observe, restored_optional, purged)。
    """
    limit = max_days if max_days is not None else watchlist_observe_max_days()
    ref = today or datetime.now().date()
    today_s = ref.isoformat()

    remaining: list[dict] = []
    restored: list[dict] = []
    purged: list[dict] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        code = extract_stock_code(row)
        if not code:
            continue
        item = dict(row)
        item["股票代码"] = code

        score_obj = score_by_code.get(code)
        if score_obj is None:
            enriched = enriched_by_code.get(code)
            if enriched:
                score_obj = engine.score_stock(ctx, enriched)
                score_by_code[code] = score_obj

        if score_obj is not None and float(getattr(score_obj, "total", 0) or 0) >= threshold:
            item["未达标连续天数"] = 0
            item["观察天数"] = 0
            item.pop("进入观察池日期", None)
            item["最后入选日期"] = today_s
            if score_obj is not None:
                item["评分"] = round(float(score_obj.total), 2)
            restored.append(item)
            continue

        days = _observe_days(item) + 1
        item["观察天数"] = days
        item["最后观察日期"] = today_s
        if score_obj is not None:
            item["评分"] = round(float(score_obj.total), 2)
        if days >= limit:
            purged.append(item)
        else:
            remaining.append(item)

    remaining.sort(
        key=lambda r: (-float(r.get("评分") or 0), str(r.get("股票代码", ""))),
    )
    return remaining, restored, purged
