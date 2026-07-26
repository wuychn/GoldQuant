"""P2 回测框架单元测试：合成行情，端到端跑通。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant.backtest2.broker import SimBroker
from quant.backtest2.costs import DEFAULT_COSTS
from quant.backtest2.engine import run_backtest
from quant.backtest2.metrics import compute_metrics
from quant.backtest2.policy import EqualWeightTopN
from quant.backtest2.tradability import can_buy, can_sell, is_suspended, limit_state, round_lot, shares_for_amount


def _make_daily(codes, n_days=60, seed=1):
    rng = np.random.default_rng(seed)
    rows = []
    dates = pd.bdate_range(end="2024-12-31", periods=n_days).strftime("%Y%m%d")
    for code in codes:
        price = 10.0
        for d in dates:
            ret = rng.normal(0.001, 0.02)
            price = max(price * (1 + ret), 1.0)
            rows.append({
                "code": code, "date": d, "open": price, "high": price * 1.01,
                "low": price * 0.99, "close": price, "volume": 1e6, "amount": 1e7,
            })
    return pd.DataFrame(rows), list(dates)


def test_tradability_helpers():
    assert round_lot(250) == 200
    assert round_lot(99) == 0
    assert shares_for_amount(10.0, 1000) == 100
    row = {"open": 11, "high": 11, "low": 11, "close": 11, "volume": 1e6}
    assert limit_state(row, 10.0) == "up"  # 11 >= 10*1.097
    assert can_buy(row, 10.0) is False
    row2 = {"open": 9, "high": 9, "low": 9, "close": 9, "volume": 1e6}
    assert limit_state(row2, 10.0) == "down"
    assert is_suspended({"open": 0, "high": 0, "low": 0, "close": 0, "volume": 0})


def test_broker_buy_sell_with_costs():
    b = SimBroker(cash=100000.0)
    row = {"date": "20240101", "open": 10, "high": 10.1, "low": 9.9, "close": 10, "volume": 1e6}
    b.buy("000001", row, None, 50000)
    assert "000001" in b.holdings
    assert b.cash < 100000  # 扣了成本
    # T+1 锁定，当日不能卖
    assert can_sell("000001", row, None, b.t1_locked) is False
    b.end_of_day()
    b.sell("000001", row, None)
    assert "000001" not in b.holdings
    assert b.cash > 0


def test_engine_end_to_end():
    daily, dates = _make_daily(["000001", "000002", "000003", "000004", "000005"], n_days=40)

    def alpha_fn(d, rows):
        # 简单 alpha：用当日涨幅的负（反转）
        out = {}
        for code, r in rows.items():
            out[code] = float(r["close"])
        return out

    broker = run_backtest(daily=daily, dates=dates, alpha_fn=alpha_fn, policy=EqualWeightTopN(n=3), initial_cash=1_000_000)
    m = compute_metrics(broker)
    assert m["n_days"] == len(dates)
    assert m["final_equity"] > 0
    # 应有成交
    assert len(broker.trades) > 0
