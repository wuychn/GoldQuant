"""买入策略：弱势门禁、近端涨幅条件过滤、concept_theme。"""

from __future__ import annotations

from quant.constants import BUY_KIND_ASCENT
from quant.gates.buy_policy import (
    buy_kind_allowed_in_regime,
    concept_theme_allows_buy,
    effective_buy_threshold,
    market_allows_new_buy,
    recent_gain_guard_allows,
    recent_gain_pct,
)
from quant.scoring.models import DimensionResult, StockScore


def _payload(up: int, down: int, idx: float = 0.0) -> dict:
    return {
        "大盘指数": [{"代码": "000001", "涨跌幅": idx}],
        "赚钱效应": {"上涨": up, "下跌": down, "涨停": 20},
    }


def _score(total: float, concept: float | None = 70.0) -> StockScore:
    dims = []
    if concept is not None:
        dims.append(DimensionResult("concept_theme", concept, 9, True))
    return StockScore(code="600000", name="测试", total=total, strategy="主升浪战法", dimensions=dims)


def test_market_blocks_when_up_down_ratio_low() -> None:
    cfg = {"weak_market": {"enabled": True, "up_down_ratio_min": 0.25, "block_new_buys": True}}
    ok, reason = market_allows_new_buy(_payload(482, 5001), cfg)
    assert not ok
    assert "上涨/下跌" in reason


def test_market_blocks_in_weak_regime() -> None:
    cfg = {"weak_market": {"enabled": True, "block_new_buys": True}}
    payload = _payload(800, 4200, idx=-1.5)
    ok, _ = market_allows_new_buy(payload, cfg)
    assert not ok


def test_effective_buy_threshold_weak_and_strong() -> None:
    weak_cfg = {
        "weak_market": {"enabled": True, "buy_threshold_delta": 3},
        "strong_market": {"enabled": True, "index_min_pct": 1.0, "zt_min": 80, "buy_threshold_delta": -2},
    }
    assert effective_buy_threshold(78.0, _payload(800, 4200, idx=-1.5), weak_cfg) == 81.0
    strong_payload = {
        "大盘指数": [{"代码": "000001", "涨跌幅": 1.2}],
        "赚钱效应": {"上涨": 3000, "下跌": 1000, "涨停": 100},
    }
    assert effective_buy_threshold(78.0, strong_payload, weak_cfg) == 76.0


def test_recent_gain_guard_blocks_high_gain_low_score() -> None:
    stock = {
        "历史行情": [{"收盘": 10.0}, {"收盘": 10.5}, {"收盘": 11.0}, {"收盘": 12.5}],
        "盘口": {"最新": 12.5},
    }
    cfg = {
        "recent_gain_guard": {
            "enabled": True,
            "apply_to_kinds": [BUY_KIND_ASCENT],
            "lookback_days": 3,
            "limits": {"震荡": 18.0},
            "exempt_min_score": 80,
        }
    }
    ok, reason = recent_gain_guard_allows(
        stock,
        _payload(2000, 2000),
        cfg,
        buy_kind=BUY_KIND_ASCENT,
        score=75.0,
    )
    assert not ok
    assert "近3日涨幅" in reason


def test_recent_gain_guard_exempts_high_score_no_decay() -> None:
    stock = {
        "历史行情": [{"收盘": 10.0}] * 25 + [{"收盘": 12.5}],
        "盘口": {"最新": 12.5, "涨幅": 2.0},
        "均线": {"ma5": 12.0, "ma10": 11.5, "ma20": 11.0},
    }
    cfg = {
        "recent_gain_guard": {
            "enabled": True,
            "apply_to_kinds": [BUY_KIND_ASCENT],
            "lookback_days": 3,
            "limits": {"震荡": 18.0},
            "exempt_min_score": 80,
            "exempt_require_no_momentum_decay": True,
        }
    }
    ok, _ = recent_gain_guard_allows(
        stock,
        _payload(2000, 2000),
        cfg,
        buy_kind=BUY_KIND_ASCENT,
        score=82.0,
    )
    assert ok


def test_block_ascent_in_weak_regime() -> None:
    cfg = {"block_ascent_in_weak_regime": True}
    payload = _payload(800, 4200, idx=-1.5)
    ok, reason = buy_kind_allowed_in_regime(BUY_KIND_ASCENT, payload, cfg)
    assert not ok
    assert "弱势" in reason


def test_concept_theme_min_score() -> None:
    cfg = {"concept_theme_min_score": 55}
    assert concept_theme_allows_buy(_score(80, 60), cfg)[0]
    assert not concept_theme_allows_buy(_score(80, 40), cfg)[0]


def test_recent_gain_pct_uses_live_price() -> None:
    stock = {
        "历史行情": [{"收盘": 10.0}, {"收盘": 10.0}, {"收盘": 10.0}, {"收盘": 10.0}],
        "盘口": {"最新": 11.5},
    }
    ret = recent_gain_pct(stock, days=3)
    assert ret is not None
    assert ret == 15.0
