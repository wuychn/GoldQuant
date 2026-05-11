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
from quant.rules.risk import checks_passed, failed_reasons, universe_checks


STRATEGY_NAME = "龙回头战法"


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
