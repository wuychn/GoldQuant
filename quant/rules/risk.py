"""Hard filters shared by all strategies."""

from __future__ import annotations

from typing import Any

from quant.config import StrategyConfig
from quant.features import float_market_cap_yi, latest_price, stock_code, stock_name
from quant.models import MarketState, RuleCheck


def universe_checks(row: dict[str, Any], config: StrategyConfig) -> list[RuleCheck]:
    code = stock_code(row)
    name = stock_name(row)
    checks = [
        RuleCheck(
            "代码前缀",
            bool(code) and code.startswith(config.universe.include_prefixes),
            f"{code} 是否属于 {config.universe.include_prefixes}",
            code,
        ),
        RuleCheck(
            "排除板块",
            not any(code.startswith(prefix) for prefix in config.universe.exclude_prefixes),
            f"{code} 不应属于 {config.universe.exclude_prefixes}",
            code,
        ),
    ]
    if config.universe.exclude_st:
        checks.append(RuleCheck("排除ST", "ST" not in name.upper(), f"{name} 不应为 ST", name))
    return checks


def market_risk_flags(market_state: MarketState, config: StrategyConfig) -> list[str]:
    if config.risk.weak_market_disable_new_buy and market_state.regime == "weak":
        return ["弱势市场禁止新开仓"]
    return []


def valuation_checks(
    row: dict[str, Any],
    *,
    max_float_market_cap_yi: float | None = None,
    max_price: float | None = None,
) -> list[RuleCheck]:
    checks: list[RuleCheck] = []
    cap = float_market_cap_yi(row)
    price = latest_price(row)
    if max_float_market_cap_yi is not None:
        checks.append(
            RuleCheck(
                "流通市值",
                cap is None or cap <= max_float_market_cap_yi,
                f"流通市值 <= {max_float_market_cap_yi}亿",
                cap,
            ),
        )
    if max_price is not None:
        checks.append(
            RuleCheck(
                "价格上限",
                price is None or price <= max_price,
                f"股价 <= {max_price}",
                price,
            ),
        )
    return checks


def checks_passed(checks: list[RuleCheck]) -> bool:
    return all(check.passed for check in checks)


def failed_reasons(checks: list[RuleCheck]) -> list[str]:
    return [f"{check.name}未通过：{check.detail}，当前={check.value}" for check in checks if not check.passed]


def summarize_failed_checks(checks: list[RuleCheck], *, limit: int = 3) -> str:
    counts: dict[str, int] = {}
    details: dict[str, str] = {}
    for check in checks:
        if check.passed:
            continue
        key = check.name
        counts[key] = counts.get(key, 0) + 1
        details.setdefault(key, check.detail)
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    return "；".join(f"{name} {count}只（{details[name]}）" for name, count in ranked)
