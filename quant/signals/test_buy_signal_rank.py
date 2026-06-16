"""买入信号：全量扫描后按评分择优。"""

from __future__ import annotations

from unittest.mock import patch

from quant.constants import STRATEGY_NAME
from quant.scoring.context import ScoreContext
from quant.scoring.models import DimensionResult, StockScore
from quant.signals.buy import generate_buy_signals


def _stock(code: str, name: str, score: float, chg: float = 3.0) -> dict:
    return {
        "股票代码": code,
        "股票名称": name,
        "战法": STRATEGY_NAME,
        "盘口": {"最新": 10.0, "涨幅": chg},
    }


def _fake_score(total: float) -> StockScore:
    return StockScore(
        code="",
        name="",
        total=total,
        strategy=STRATEGY_NAME,
        dimensions=[DimensionResult("main_wave", 80, 22, True)],
    )


@patch("quant.signals.buy.allocate_buy_quantities_by_score", return_value={"000002": 100})
@patch("quant.signals.buy.intraday_allows_buy", return_value=(True, ""))
@patch("quant.signals.buy.detect_buy_setup", return_value=(True, "上升途中", "ok"))
@patch("quant.signals.buy.trend_allows_buy", return_value=(True, ""))
@patch("quant.signals.buy.check_buy_gates")
@patch("quant.signals.buy.active_holding_count", return_value=0)
@patch("quant.signals.buy.position_limits", return_value={"max_stocks": 1})
@patch("quant.signals.buy.get_holdings", return_value=[])
def test_picks_highest_score_not_list_order(*mocks) -> None:
    gate = mocks[3]
    gate.return_value.passed = True

    low = _stock("000001", "低分", 72.0)
    high = _stock("000002", "高分", 88.0)
    ctx = ScoreContext(payload={"自选股": [low, high]}, mode="during_market")

    def fake_score(ctx_arg, stock):
        return _fake_score(72.0 if stock["股票代码"] == "000001" else 88.0)

    with patch("quant.signals.buy.ScoringEngine") as eng_cls:
        eng_cls.return_value.config = {"buy_threshold": 72}
        eng_cls.return_value.score_stock.side_effect = fake_score
        signals = generate_buy_signals(ctx, mode="during_market")

    assert len(signals) == 1
    assert signals[0].code == "000002"


@patch(
    "quant.signals.buy.allocate_buy_quantities_by_score",
    return_value={"000002": 100, "000003": 100},
)
@patch("quant.signals.buy.intraday_allows_buy", return_value=(True, ""))
@patch("quant.signals.buy.detect_buy_setup", return_value=(True, "上升途中", "ok"))
@patch("quant.signals.buy.trend_allows_buy", return_value=(True, ""))
@patch("quant.signals.buy.check_buy_gates")
@patch("quant.signals.buy.active_holding_count", return_value=0)
@patch("quant.signals.buy.position_limits", return_value={"max_stocks": 2})
@patch("quant.signals.buy.get_holdings", return_value=[])
def test_returns_top_n_by_score(*mocks) -> None:
    gate = mocks[3]
    gate.return_value.passed = True

    stocks = [
        _stock("000001", "A", 75.0),
        _stock("000002", "B", 90.0),
        _stock("000003", "C", 80.0),
    ]
    ctx = ScoreContext(payload={"自选股": stocks}, mode="during_market")
    score_map = {"000001": 75.0, "000002": 90.0, "000003": 80.0}

    with patch("quant.signals.buy.ScoringEngine") as eng_cls:
        eng_cls.return_value.config = {"buy_threshold": 72}
        eng_cls.return_value.score_stock.side_effect = (
            lambda _ctx, s: _fake_score(score_map[s["股票代码"]])
        )
        signals = generate_buy_signals(ctx, mode="during_market")

    assert [s.code for s in signals] == ["000002", "000003"]
