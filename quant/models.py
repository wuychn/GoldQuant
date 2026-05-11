"""Shared dataclasses for deterministic strategy output."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


MarketRegime = Literal["strong", "neutral", "weak", "unknown"]
SignalAction = Literal[
    "add_optional",
    "buy_watch",
    "hold",
    "sell_watch",
    "avoid",
]


@dataclass(frozen=True)
class RuleCheck:
    name: str
    passed: bool
    detail: str
    value: Any = None


@dataclass(frozen=True)
class MarketState:
    regime: MarketRegime
    score: int
    passed_rules: list[str]
    failed_rules: list[str]
    raw_votes: dict[str, int]


@dataclass(frozen=True)
class StockSignal:
    stock_code: str
    stock_name: str
    strategy: str
    action: SignalAction
    score: int
    reasons: list[str]
    risk_flags: list[str] = field(default_factory=list)
    buy_price_range: tuple[float, float] | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    checks: list[RuleCheck] = field(default_factory=list)


@dataclass(frozen=True)
class SignalReport:
    strategy_version: str
    mode: str
    market_state: MarketState
    signals: list[StockSignal]
    risk_flags: list[str] = field(default_factory=list)
    no_signal_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
