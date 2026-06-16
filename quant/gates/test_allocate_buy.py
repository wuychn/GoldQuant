"""按评分比例分配买入预算。"""

from __future__ import annotations

from unittest.mock import patch

from quant.gates.rules import allocate_buy_quantities_by_score
from quant.scoring.context import ScoreContext


@patch("quant.gates.rules.get_cash", return_value=100_000.0)
@patch("quant.gates.rules.get_total_assets", return_value=100_000.0)
@patch("quant.gates.rules.compute_holdings_market_value", return_value=0.0)
@patch("quant.gates.rules.active_holding_count", return_value=0)
@patch(
    "quant.gates.rules.position_limits",
    return_value={"total_pct": 50.0, "max_stocks": 2, "single_pct": {}},
)
def test_proportional_budget_by_score(*_mocks) -> None:
    ctx = ScoreContext(payload={}, mode="during_market")
    candidates = [
        (90.0, {"股票代码": "000001", "战法": "主升浪"}, 10.0),
        (60.0, {"股票代码": "000002", "战法": "主升浪"}, 10.0),
    ]
    qty = allocate_buy_quantities_by_score(candidates, ctx)
    assert qty["000001"] > qty["000002"]
    assert qty["000001"] % 100 == 0
    assert qty["000002"] % 100 == 0
