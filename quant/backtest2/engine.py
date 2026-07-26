"""回测主引擎：日频循环。

每个交易日 T：
1. 取 universe(T) 当日行情 + 前一日收盘
2. 算 alpha（由调用方提供 alpha_fn 或预构建面板）
3. target_weights = policy.target_weights(alpha, prices, current, T)
4. 差分 → 买卖单 → broker 撮合（T 日收盘成交）
5. broker.record_equity(T, prices)
6. broker.end_of_day()（释放 T+1 锁）
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from quant.backtest2.broker import SimBroker
from quant.backtest2.policy import PortfolioPolicy
from quant.backtest2.tradability import shares_for_amount


AlphaFn = Callable[[str, dict[str, dict]], dict[str, float]]


def _prev_close_map(daily: pd.DataFrame, code: str, as_of: str) -> float | None:
    sub = daily[(daily["code"] == code) & (daily["date"] < as_of)].sort_values("date")
    if sub.empty:
        return None
    return float(pd.to_numeric(sub["close"], errors="coerce").iloc[-1])


def run_backtest(
    *,
    daily: pd.DataFrame,  # columns: code,date,open,high,low,close,volume,...
    dates: list[str],
    alpha_fn: AlphaFn,
    policy: PortfolioPolicy,
    initial_cash: float = 1_000_000.0,
    max_positions: int = 10,
) -> SimBroker:
    broker = SimBroker(cash=initial_cash)

    for d in dates:
        day_rows = daily[daily["date"] == d]
        if day_rows.empty:
            continue
        rows_by_code: dict[str, dict] = {}
        prices: dict[str, float] = {}
        prev_closes: dict[str, float | None] = {}
        for _, r in day_rows.iterrows():
            code = str(r["code"])
            rows_by_code[code] = r.to_dict()
            prices[code] = float(r["close"])
            prev_closes[code] = _prev_close_map(daily, code, d)

        alpha = alpha_fn(d, rows_by_code)
        if not alpha:
            broker.record_equity(d, prices)
            broker.end_of_day()
            continue

        # 当前持仓权重
        eq = broker.total_equity(prices) or 1.0
        current = {}
        for code, h in broker.holdings.items():
            p = prices.get(code, 0.0)
            current[code] = p * h.shares / eq

        target = policy.target_weights(alpha, prices, current, d)
        # 限制持仓数
        if max_positions and len(target) > max_positions:
            target = dict(sorted(target.items(), key=lambda kv: -kv[1])[:max_positions])

        # 先卖后买
        target_codes = set(target.keys())
        for code in list(broker.holdings.keys()):
            tw = target.get(code, 0.0)
            cur_w = current.get(code, 0.0)
            if tw < cur_w - 1e-6 and code in rows_by_code:
                # 减仓或清仓
                target_value = tw * eq
                cur_value = prices.get(code, 0.0) * broker.holdings[code].shares
                excess = cur_value - target_value
                if excess > 0:
                    sell_shares = shares_for_amount(prices.get(code, 0.0), excess)
                    if sell_shares > 0:
                        broker.sell(code, rows_by_code[code], prev_closes.get(code), target_shares=sell_shares)

        # 买入
        eq = broker.total_equity(prices) or 1.0
        for code, tw in target.items():
            row = rows_by_code.get(code)
            if row is None:
                continue
            cur_shares = broker.holdings[code].shares if code in broker.holdings else 0
            cur_value = prices.get(code, 0.0) * cur_shares
            target_value = tw * eq
            excess = target_value - cur_value
            if excess > 0:
                broker.buy(code, row, prev_closes.get(code), excess)

        broker.record_equity(d, prices)
        broker.end_of_day()

    return broker
