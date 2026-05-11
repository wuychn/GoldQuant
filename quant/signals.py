"""Top-level deterministic signal generation."""

from __future__ import annotations

from typing import Any

from quant.config import StrategyConfig
from quant.features import unwrap_payload
from quant.market_state import classify_market_state
from quant.models import SignalReport, StockSignal
from quant.rules.lht import evaluate_lht_candidates, explain_lht_no_signal
from quant.rules.risk import market_risk_flags
from quant.rules.zt import evaluate_zt_candidates, explain_zt_no_signal


def _dedupe(signals: list[StockSignal]) -> list[StockSignal]:
    best: dict[tuple[str, str], StockSignal] = {}
    for signal in signals:
        key = (signal.stock_code, signal.strategy)
        current = best.get(key)
        if current is None or signal.score > current.score:
            best[key] = signal
    return sorted(best.values(), key=lambda x: (-x.score, x.strategy, x.stock_code))


def generate_signal_report(
    raw_payload: dict[str, Any],
    *,
    mode: str,
    config: StrategyConfig,
) -> SignalReport:
    payload = unwrap_payload(raw_payload)
    market_state = classify_market_state(payload, config)
    risk_flags = market_risk_flags(market_state, config)

    signals: list[StockSignal] = []
    no_signal_reasons: list[str] = []
    if not risk_flags:
        signals.extend(evaluate_zt_candidates(payload, config, mode=mode))
        signals.extend(evaluate_lht_candidates(payload, config, mode=mode))
    else:
        no_signal_reasons.extend(f"全局风控未通过：{flag}" for flag in risk_flags)

    deduped = _dedupe(signals)
    if not deduped and not risk_flags:
        no_signal_reasons.extend(
            [
                explain_zt_no_signal(payload, config),
                explain_lht_no_signal(payload, config),
            ],
        )

    return SignalReport(
        strategy_version=config.version,
        mode=mode,
        market_state=market_state,
        signals=deduped,
        risk_flags=risk_flags,
        no_signal_reasons=no_signal_reasons,
    )
