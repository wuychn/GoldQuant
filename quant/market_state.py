"""Deterministic market regime classification."""

from __future__ import annotations

from typing import Any

from quant.config import StrategyConfig
from quant.features import to_float, unwrap_payload
from quant.models import MarketState


def _vote_from_value(value: float | None, strong: float, weak: float) -> str | None:
    if value is None:
        return None
    if value > strong:
        return "strong"
    if value < weak:
        return "weak"
    return "neutral"


def _find_index_pct(payload: dict[str, Any], name: str = "上证") -> float | None:
    indexes = payload.get("大盘指数")
    if not isinstance(indexes, list):
        return None
    for row in indexes:
        if not isinstance(row, dict):
            continue
        row_name = str(row.get("名称") or "")
        if name in row_name:
            return to_float(row.get("涨跌幅"))
    return None


def classify_market_state(raw_payload: dict[str, Any], config: StrategyConfig) -> MarketState:
    payload = unwrap_payload(raw_payload)
    state = payload.get("市场状态机")
    state = state if isinstance(state, dict) else {}
    zt_stats = state.get("今日涨停统计")
    zt_stats = zt_stats if isinstance(zt_stats, dict) else payload.get("今日涨停统计")
    zt_stats = zt_stats if isinstance(zt_stats, dict) else {}

    votes = {"strong": 0, "neutral": 0, "weak": 0}
    passed: list[str] = []
    failed: list[str] = []

    checks = {
        "上证实时涨跌": _vote_from_value(_find_index_pct(payload), strong=0.5, weak=-0.5),
        "涨停家数": _vote_from_value(to_float(zt_stats.get("涨停家数")), strong=60, weak=30),
        "连板高度": _vote_from_value(to_float(zt_stats.get("市场最高连板数")), strong=4.9, weak=2.1),
    }

    machine_index = state.get("上证指数")
    if isinstance(machine_index, dict):
        checks["上证较20日均线"] = _vote_from_value(
            to_float(machine_index.get("收盘较20日均线")),
            strong=1,
            weak=-1,
        )

    yesterday = state.get("昨日涨停表现")
    if isinstance(yesterday, dict):
        checks["昨日涨停表现"] = _vote_from_value(
            to_float(yesterday.get("涨跌幅均值")),
            strong=2,
            weak=0,
        )

    amount = state.get("两市成交额近似")
    if isinstance(amount, dict):
        checks["两市成交倍率"] = _vote_from_value(
            to_float(amount.get("今日相对近5日均倍率")),
            strong=1.1,
            weak=0.9,
        )

    for name, vote in checks.items():
        if vote:
            votes[vote] += 1
            passed.append(f"{name}:{vote}")
        else:
            failed.append(f"{name}:missing")

    min_votes = config.market_state.min_same_direction_votes
    regime = "unknown"
    for candidate in ("strong", "neutral", "weak"):
        if votes[candidate] >= min_votes:
            regime = candidate
            break
    if regime == "unknown" and any(votes.values()):
        regime = max(votes, key=votes.get)

    score_map = {"strong": 80, "neutral": 50, "weak": 20, "unknown": 0}
    return MarketState(
        regime=regime,  # type: ignore[arg-type]
        score=score_map[regime],
        passed_rules=passed,
        failed_rules=failed,
        raw_votes=votes,
    )
