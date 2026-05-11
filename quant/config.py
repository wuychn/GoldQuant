"""Strategy configuration loading.

The strategy file uses a small YAML subset so it can carry comments without
adding a runtime dependency. Supported values: strings, booleans, numbers,
inline arrays, and one-level nested dictionaries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULT_STRATEGY_CONFIG = PACKAGE_ROOT / "strategy.yml"


@dataclass(frozen=True)
class UniverseConfig:
    include_prefixes: tuple[str, ...] = ("60", "00", "30")
    exclude_prefixes: tuple[str, ...] = ("688", "8")
    exclude_st: bool = True
    min_listing_days: int = 60


@dataclass(frozen=True)
class RiskConfig:
    max_holding_days: int = 5
    stoploss_cooldown_days: int = 3
    weak_market_disable_new_buy: bool = True
    max_single_position_pct: float = 0.1


@dataclass(frozen=True)
class MarketStateConfig:
    strong_position_limit: float = 0.8
    neutral_position_limit: float = 0.5
    weak_position_limit: float = 0.2
    min_same_direction_votes: int = 3


@dataclass(frozen=True)
class ZtStrategyConfig:
    enabled: bool = True
    max_float_market_cap_yi: float = 200
    max_price: float = 20
    max_popularity_rank: int = 20
    min_consecutive_limit_up: int = 2
    min_score_to_signal: int = 70


@dataclass(frozen=True)
class LhtStrategyConfig:
    enabled: bool = True
    max_popularity_rank: int = 50
    min_pullback_pct: float = 10
    min_volume_ratio: float = 1.2
    min_score_to_signal: int = 70


@dataclass(frozen=True)
class StrategyConfig:
    version: str = "v1"
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    market_state: MarketStateConfig = field(default_factory=MarketStateConfig)
    zt_strategy: ZtStrategyConfig = field(default_factory=ZtStrategyConfig)
    lht_strategy: LhtStrategyConfig = field(default_factory=LhtStrategyConfig)


def _tuple(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(x) for x in value)
    if isinstance(value, tuple):
        return tuple(str(x) for x in value)
    return default


def _strip_inline_comment(line: str) -> str:
    in_single = False
    in_double = False
    for idx, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return line[:idx].rstrip()
    return line.rstrip()


def _parse_scalar(raw: str) -> Any:
    text = raw.strip()
    if text == "":
        return ""
    if text in {"true", "True"}:
        return True
    if text in {"false", "False"}:
        return False
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part.strip()) for part in inner.split(",")]
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        return text


def _load_strategy_file(path: Path) -> dict[str, Any]:
    root: dict[str, Any] = {}
    current_section: dict[str, Any] | None = None
    with path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            if not raw_line.strip() or raw_line.lstrip().startswith("#"):
                continue
            line = _strip_inline_comment(raw_line.rstrip("\n"))
            if not line.strip():
                continue
            indent = len(line) - len(line.lstrip(" "))
            key, sep, value = line.strip().partition(":")
            if not sep:
                continue
            key = key.strip()
            value = value.strip()
            if indent == 0 and value == "":
                section: dict[str, Any] = {}
                root[key] = section
                current_section = section
            elif indent == 0:
                root[key] = _parse_scalar(value)
                current_section = None
            elif current_section is not None:
                current_section[key] = _parse_scalar(value)
    return root


def strategy_config_from_dict(raw: dict[str, Any] | None) -> StrategyConfig:
    raw = raw or {}
    universe = raw.get("universe") if isinstance(raw.get("universe"), dict) else {}
    risk = raw.get("risk") if isinstance(raw.get("risk"), dict) else {}
    market_state = raw.get("market_state") if isinstance(raw.get("market_state"), dict) else {}
    zt = raw.get("zt_strategy") if isinstance(raw.get("zt_strategy"), dict) else {}
    lht = raw.get("lht_strategy") if isinstance(raw.get("lht_strategy"), dict) else {}

    default_universe = UniverseConfig()
    return StrategyConfig(
        version=str(raw.get("version") or "v1"),
        universe=UniverseConfig(
            include_prefixes=_tuple(universe.get("include_prefixes"), default_universe.include_prefixes),
            exclude_prefixes=_tuple(universe.get("exclude_prefixes"), default_universe.exclude_prefixes),
            exclude_st=bool(universe.get("exclude_st", default_universe.exclude_st)),
            min_listing_days=int(universe.get("min_listing_days", default_universe.min_listing_days)),
        ),
        risk=RiskConfig(**{**RiskConfig().__dict__, **risk}),
        market_state=MarketStateConfig(**{**MarketStateConfig().__dict__, **market_state}),
        zt_strategy=ZtStrategyConfig(**{**ZtStrategyConfig().__dict__, **zt}),
        lht_strategy=LhtStrategyConfig(**{**LhtStrategyConfig().__dict__, **lht}),
    )


def load_strategy_config(path: str | Path | None = None) -> StrategyConfig:
    path = DEFAULT_STRATEGY_CONFIG if path is None else path
    cfg_path = Path(path)
    if not cfg_path.is_file():
        return StrategyConfig()
    return strategy_config_from_dict(_load_strategy_file(cfg_path))
