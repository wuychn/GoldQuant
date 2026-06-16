"""强市买入放宽策略。"""

from __future__ import annotations

from quant.gates.buy_policy import (
    effective_buy_threshold,
    effective_max_change_pct,
    is_strong_market,
)


def _payload(idx: float, zt: int, up: int = 3000, down: int = 1000) -> dict:
    return {
        "大盘指数": [{"代码": "000001", "涨跌幅": idx}],
        "赚钱效应": {"上涨": up, "下跌": down, "涨停": zt},
    }


def test_strong_market_when_index_and_zt_high() -> None:
    cfg = {"strong_market": {"enabled": True, "index_min_pct": 1.0, "zt_min": 80}}
    assert is_strong_market(_payload(1.2, 100), cfg)


def test_not_strong_when_index_low() -> None:
    cfg = {"strong_market": {"enabled": True, "index_min_pct": 1.0, "zt_min": 80}}
    assert not is_strong_market(_payload(0.5, 100), cfg)


def test_effective_buy_threshold_lower_in_strong_market() -> None:
    cfg = {
        "strong_market": {
            "enabled": True,
            "index_min_pct": 1.0,
            "zt_min": 80,
            "buy_threshold_delta": -2,
        }
    }
    payload = _payload(1.2, 100)
    assert effective_buy_threshold(72.0, payload, cfg) == 70.0


def test_effective_max_change_pct_higher_in_strong_market() -> None:
    cfg = {
        "max_change_pct": 8.0,
        "strong_market": {
            "enabled": True,
            "index_min_pct": 1.0,
            "zt_min": 80,
            "max_change_pct": 9.5,
        },
    }
    payload = _payload(1.2, 100)
    assert effective_max_change_pct(cfg, payload) == 9.5
