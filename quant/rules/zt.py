"""Limit-up continuation strategy rules."""

from __future__ import annotations

from typing import Any

from quant.config import StrategyConfig
from quant.features import (
    index_by_code,
    latest_price,
    popularity_rank,
    stock_code,
    stock_name,
    to_int,
)
from quant.models import RuleCheck, StockSignal
from quant.rules.risk import checks_passed, failed_reasons, summarize_failed_checks, universe_checks, valuation_checks


STRATEGY_NAME = "涨停板战法"


def _limit_up_rows(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = payload.get("涨停统计") or payload.get("今日涨停统计") or []
    if isinstance(rows, dict):
        rows = rows.get("rows") or rows.get("明细") or rows.get("涨停明细") or []
    return index_by_code([x for x in rows if isinstance(x, dict)]) if isinstance(rows, list) else {}


def _merge_limit_up_info(row: dict[str, Any], limit_up: dict[str, dict[str, Any]]) -> dict[str, Any]:
    code = stock_code(row)
    extra = limit_up.get(code, {})
    merged = dict(row)
    for key, value in extra.items():
        merged.setdefault(key, value)
    return merged


def _candidate_checks_and_score(
    row: dict[str, Any],
    limit_up: dict[str, dict[str, Any]],
    config: StrategyConfig,
) -> tuple[list[RuleCheck], int, int | None, int | None, str]:
    cfg = config.zt_strategy
    rank = popularity_rank(row)
    consecutive = to_int(row.get("连板数") or row.get("连续涨停天数"))
    code = stock_code(row)

    checks: list[RuleCheck] = []
    checks.extend(universe_checks(row, config))
    checks.extend(valuation_checks(row, max_float_market_cap_yi=cfg.max_float_market_cap_yi, max_price=cfg.max_price))
    checks.append(
        RuleCheck(
            "人气排名",
            rank is not None and rank <= cfg.max_popularity_rank,
            f"人气排名 <= {cfg.max_popularity_rank}",
            rank,
        ),
    )
    checks.append(
        RuleCheck(
            "涨停明细",
            code in limit_up,
            "必须出现在涨停统计明细中",
            code,
        ),
    )
    checks.append(
        RuleCheck(
            "连板数",
            consecutive is not None and consecutive >= cfg.min_consecutive_limit_up,
            f"连板数 >= {cfg.min_consecutive_limit_up}",
            consecutive,
        ),
    )

    score = 40
    score += 15 if rank is not None and rank <= min(10, cfg.max_popularity_rank) else 0
    score += 15 if consecutive is not None and consecutive >= cfg.min_consecutive_limit_up else 0
    score += 15 if code in limit_up else 0
    score += 15 if checks_passed(checks[: len(checks) - 3]) else 0
    return checks, score, rank, consecutive, code


def evaluate_zt_candidates(
    payload: dict[str, Any],
    config: StrategyConfig,
    *,
    mode: str,
) -> list[StockSignal]:
    cfg = config.zt_strategy
    if not cfg.enabled:
        return []

    hot_rows = payload.get("同花顺人气榜")
    if not isinstance(hot_rows, list):
        return []

    limit_up = _limit_up_rows(payload)
    signals: list[StockSignal] = []
    for raw in hot_rows:
        if not isinstance(raw, dict):
            continue
        row = _merge_limit_up_info(raw, limit_up)
        checks, score, rank, consecutive, code = _candidate_checks_and_score(row, limit_up, config)
        if not checks_passed(checks):
            continue
        if score < cfg.min_score_to_signal:
            continue

        price = latest_price(row)
        buy_range = (round(price * 0.99, 3), round(price * 1.01, 3)) if price else None
        signals.append(
            StockSignal(
                stock_code=code,
                stock_name=stock_name(row),
                strategy=STRATEGY_NAME,
                action="add_optional" if mode.startswith("post_market") else "buy_watch",
                score=min(score, 100),
                buy_price_range=buy_range,
                stop_loss=round(price * 0.93, 3) if price else None,
                take_profit=round(price * 1.12, 3) if price else None,
                reasons=[
                    f"人气排名{rank}通过",
                    f"连板数{consecutive}通过",
                    "涨停统计确认",
                ],
                risk_flags=failed_reasons(checks),
                checks=checks,
            ),
        )
    return signals


def explain_zt_no_signal(payload: dict[str, Any], config: StrategyConfig) -> str:
    cfg = config.zt_strategy
    if not cfg.enabled:
        return f"{STRATEGY_NAME}：策略未启用"

    hot_rows = payload.get("同花顺人气榜")
    if not isinstance(hot_rows, list):
        return f"{STRATEGY_NAME}：缺少同花顺人气榜数据"
    if not hot_rows:
        return f"{STRATEGY_NAME}：同花顺人气榜为空"

    limit_up = _limit_up_rows(payload)
    checked = 0
    score_failed = 0
    failed_checks: list[RuleCheck] = []
    for raw in hot_rows:
        if not isinstance(raw, dict):
            continue
        checked += 1
        row = _merge_limit_up_info(raw, limit_up)
        checks, score, _, _, _ = _candidate_checks_and_score(row, limit_up, config)
        failed_checks.extend(check for check in checks if not check.passed)
        if checks_passed(checks) and score < cfg.min_score_to_signal:
            score_failed += 1

    if checked == 0:
        return f"{STRATEGY_NAME}：同花顺人气榜没有有效股票行"

    parts = [f"{STRATEGY_NAME}：评估 {checked} 只，0 只通过"]
    summary = summarize_failed_checks(failed_checks, limit=3)
    if summary:
        parts.append(f"主要未通过：{summary}")
    if score_failed:
        parts.append(f"{score_failed} 只低于最低信号评分 {cfg.min_score_to_signal}")
    return "；".join(parts)
