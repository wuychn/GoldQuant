"""P2 回测框架单元测试：合成行情，端到端跑通。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant.backtest2.broker import SimBroker
from quant.backtest2.costs import DEFAULT_COSTS
from quant.backtest2.engine import ExitConfig, run_backtest
from quant.backtest2.metrics import compute_metrics
from quant.backtest2.policy import EqualWeightTopN
from quant.backtest2.tradability import (
    can_buy,
    can_sell,
    is_suspended,
    limit_state,
    price_at_limit,
    round_lot,
    shares_for_amount,
)


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


def test_nan_suspended():
    """NaN volume/OHLC 不应被当作可交易（数据缺失保守判停牌）。"""
    nan = float("nan")
    assert is_suspended({"open": 10, "high": 10, "low": 10, "close": 10, "volume": nan})
    assert is_suspended({"open": nan, "high": nan, "low": nan, "close": nan, "volume": nan})
    # 仍保持：vol=0 / OHLC 全 0 判停牌（不破坏旧用例）
    assert is_suspended({"open": 0, "high": 0, "low": 0, "close": 0, "volume": 0})
    # 有效行情不停牌
    assert not is_suspended({"open": 10, "high": 10.1, "low": 9.9, "close": 10, "volume": 1e6})


def test_price_at_limit_open():
    """给定成交价（开盘/最新）是否触及涨跌停价——供 strict T 开盘成交判定。"""
    assert price_at_limit(11.0, 10.0, code="000001") == "up"
    assert price_at_limit(9.0, 10.0, code="000001") == "down"
    assert price_at_limit(10.5, 10.0, code="000001") == "none"


def test_slippage_adv_effect():
    """vol_scaled 模式下，预构造 ctx 的高参与率笔应产生更大冲击滑点。"""
    from types import SimpleNamespace

    from quant.execution.slippage import SlippageContext, slip_price_with_context

    cfg = SimpleNamespace(slippage_model="vol_scaled", slippage_pct=0.001, slippage_max_pct=0.02)
    # 高参与率（本笔占 ADV 10%）→ ADV 冲击档 +0.002
    heavy = SlippageContext(volatility_pct=2.0, amount=1e7, day_change_pct=None,
                            limit_pct=9.9, adv_amount=1e8, participation=0.1)
    # 低参与率（<2%）→ 无 ADV 档
    light = SlippageContext(volatility_pct=2.0, amount=1e7, day_change_pct=None,
                            limit_pct=9.9, adv_amount=1e10, participation=0.001)
    p_heavy = slip_price_with_context(10.0, side="buy", cfg=cfg, ctx=heavy)
    p_light = slip_price_with_context(10.0, side="buy", cfg=cfg, ctx=light)
    assert p_heavy > p_light, "高参与率应产生更大冲击滑点"
    # ctx=None 退化路径不崩，返回有限正值
    p_default = slip_price_with_context(10.0, side="buy", cfg=cfg)
    assert p_default > 0 and p_default == p_default


def test_strict_exit_timing():
    """strict 模式：大跌当日（T）不应卖出（避免 T 收盘前视），应在下一交易日 T+1 开盘卖。"""
    dates = pd.bdate_range("2024-12-02", periods=27).strftime("%Y-%m-%d").tolist()
    d_bigdrop, d_next = dates[-2], dates[-1]
    rows = []
    # 其他票全程 close=5，确保 A 始终是 alpha top1
    for code in ["000002", "000003", "000004", "000005"]:
        for d in dates:
            rows.append({"code": code, "date": d, "open": 5, "high": 5.01, "low": 4.99,
                         "close": 5, "volume": 1e6, "amount": 1e7})
    # A：前 25 天横盘 10，倒数第 2 天单日大跌 close=8（open=10），末日 open=7.5
    for i, d in enumerate(dates):
        if i < len(dates) - 2:
            o = h = l = c = 10.0
        elif i == len(dates) - 2:
            o, h, l, c = 10.0, 10.0, 7.9, 8.0
        else:
            o, h, l, c = 7.5, 7.6, 6.9, 7.0
        rows.append({"code": "000001", "date": d, "open": o, "high": h, "low": l,
                     "close": c, "volume": 1e6, "amount": 1e7})
    daily = pd.DataFrame(rows)

    def alpha_fn(d, rows_):
        return {c: float(r["close"]) for c, r in rows_.items()}

    broker = run_backtest(daily=daily, dates=dates, alpha_fn=alpha_fn,
                          policy=EqualWeightTopN(n=1), initial_cash=1_000_000, exit_config=ExitConfig())
    sells_a = [t for t in broker.trades if t.code == "000001" and t.side == "sell"]
    assert sells_a, "A 应被止损卖出"
    # 关键：大跌当日（d_bigdrop）不应有卖出（strict 不用 T 收盘决策）
    assert not any(t.date == d_bigdrop for t in sells_a), "strict 下大跌当日不应卖出（前视）"
    # 应在下一交易日卖出
    assert sells_a[-1].date == d_next
    # 卖出价应接近 d_next 开盘 7.5（滑点后略低），而非 d_bigdrop 收盘 8.0
    assert 7.0 < sells_a[-1].price < 7.6, f"卖出价 {sells_a[-1].price} 应接近 T+1 开盘 7.5"
