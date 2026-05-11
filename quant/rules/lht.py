"""Dragon-head pullback strategy rules."""

from __future__ import annotations

from typing import Any

from quant.config import StrategyConfig
from quant.features import (
    has_recent_limit_up,
    latest_price,
    max_pullback_pct,
    popularity_rank,
    stock_code,
    stock_name,
    volume_ratio,
)
from quant.models import RuleCheck, StockSignal
from quant.rules.risk import checks_passed, failed_reasons, summarize_failed_checks, universe_checks


STRATEGY_NAME = "龙回头战法"


def _candidate_checks_and_score(
    row: dict[str, Any],
    config: StrategyConfig,
) -> tuple[list[RuleCheck], int, int | None, float | None, float | None, bool]:
    cfg = config.lht_strategy
    rank = popularity_rank(row)
    pullback = max_pullback_pct(row)
    vol_ratio = volume_ratio(row)
    recent_limit_up = has_recent_limit_up(row)
    checks: list[RuleCheck] = []
    checks.extend(universe_checks(row, config))
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
            "近期涨停",
            recent_limit_up,
            "近30日应出现涨停或接近涨停",
            recent_limit_up,
        ),
    )
    checks.append(
        RuleCheck(
            "回撤幅度",
            pullback is not None and pullback >= cfg.min_pullback_pct,
            f"高点回撤 >= {cfg.min_pullback_pct}%",
            pullback,
        ),
    )
    checks.append(
        RuleCheck(
            "量能确认",
            vol_ratio is None or vol_ratio >= cfg.min_volume_ratio,
            f"量比 >= {cfg.min_volume_ratio}，缺失时不强制否决",
            vol_ratio,
        ),
    )

    score = 35
    score += 15 if rank is not None and rank <= cfg.max_popularity_rank else 0
    score += 20 if recent_limit_up else 0
    score += 20 if pullback is not None and pullback >= cfg.min_pullback_pct else 0
    score += 10 if vol_ratio is None or vol_ratio >= cfg.min_volume_ratio else 0
    return checks, score, rank, pullback, vol_ratio, recent_limit_up


def evaluate_lht_candidates(
    payload: dict[str, Any],
    config: StrategyConfig,
    *,
    mode: str,
) -> list[StockSignal]:
    cfg = config.lht_strategy
    if not cfg.enabled:
        return []

    rows = payload.get("同花顺人气榜")
    if not isinstance(rows, list):
        return []

    signals: list[StockSignal] = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        checks, score, rank, pullback, _, _ = _candidate_checks_and_score(row, config)
        if not checks_passed(checks):
            continue
        if score < cfg.min_score_to_signal:
            continue

        price = latest_price(row)
        signals.append(
            StockSignal(
                stock_code=stock_code(row),
                stock_name=stock_name(row),
                strategy=STRATEGY_NAME,
                action="add_optional" if mode.startswith("post_market") else "buy_watch",
                score=min(score, 100),
                buy_price_range=(round(price * 0.985, 3), round(price * 1.015, 3)) if price else None,
                stop_loss=round(price * 0.92, 3) if price else None,
                take_profit=round(price * 1.15, 3) if price else None,
                reasons=[
                    f"人气排名{rank}通过",
                    "近30日涨停记忆通过",
                    f"回撤{pullback:.2f}%通过" if pullback is not None else "回撤数据缺失",
                ],
                risk_flags=failed_reasons(checks),
                checks=checks,
            ),
        )
    return signals


def explain_lht_no_signal(payload: dict[str, Any], config: StrategyConfig) -> str:
    cfg = config.lht_strategy
    if not cfg.enabled:
        return f"{STRATEGY_NAME}：策略未启用"

    rows = payload.get("同花顺人气榜")
    if not isinstance(rows, list):
        return f"{STRATEGY_NAME}：缺少同花顺人气榜数据"
    if not rows:
        return f"{STRATEGY_NAME}：同花顺人气榜为空"

    checked = 0
    score_failed = 0
    failed_checks: list[RuleCheck] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        checked += 1
        checks, score, _, _, _, _ = _candidate_checks_and_score(row, config)
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
