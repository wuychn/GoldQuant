"""仓位分配 v2 测试。"""

from __future__ import annotations

from quant.execution.sim_rules import calc_buy_cost, load_trade_sim_config
from quant.portfolio.allocator import allocate_buy_quantities_by_score
from quant.scoring.context import ScoreContext


def test_allocator_uses_cost_not_nominal_price(monkeypatch):
    ctx = ScoreContext.from_payload(
        {
            "大盘指数": [{"代码": "000001", "涨跌幅": 0.5}],
            "赚钱效应": {"上涨": 2000, "下跌": 1500},
        }
    )
    monkeypatch.setattr("quant.portfolio.allocator.get_total_assets", lambda: 100_000.0)
    monkeypatch.setattr("quant.portfolio.allocator.get_cash", lambda: 50_000.0)
    monkeypatch.setattr("quant.portfolio.allocator.get_holdings", lambda: [])
    monkeypatch.setattr("quant.portfolio.allocator.active_holding_count", lambda: 0)
    monkeypatch.setattr(
        "quant.portfolio.allocator.compute_holdings_market_value",
        lambda _h: 0.0,
    )

    stock = {"股票代码": "600000", "股票名称": "测试", "战法": "主升浪"}
    out = allocate_buy_quantities_by_score([(80.0, stock, 10.0)], ctx)
    qty = out.get("600000", 0)
    assert qty >= 100
    sim = load_trade_sim_config()
    cost = calc_buy_cost(10.0, qty, "600000", sim, stock=stock)
    assert cost.total <= 50_000.0 + 1e-6
