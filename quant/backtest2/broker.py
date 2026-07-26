"""模拟撮合器（SimBroker）：现金 + 持仓 + T+1 锁定 + 成本。

每日流程：下单 → 次日开盘/收盘撮合 → 更新持仓与现金。
本实现采用「T 日收盘决策，T 日收盘成交」简化口径（与离线库日频对齐），
T+1 通过 t1_locked 集合实现：当日新买入次日才可卖。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quant.backtest2.costs import CostModel, DEFAULT_COSTS
from quant.backtest2.tradability import (
    can_buy,
    can_sell,
    is_suspended,
    limit_state,
    round_lot,
    shares_for_amount,
)


@dataclass
class Holding:
    code: str
    shares: int
    cost_price: float  # 含成本买入均价
    buy_date: str = ""


@dataclass
class Trade:
    date: str
    code: str
    side: str  # 'buy' / 'sell'
    shares: int
    price: float
    cost: float
    pnl: float = 0.0  # 卖出时记录已实现盈亏


@dataclass
class SimBroker:
    cash: float
    costs: CostModel = field(default_factory=lambda: DEFAULT_COSTS)

    holdings: dict[str, Holding] = field(default_factory=dict)
    t1_locked: set[str] = field(default_factory=set)  # 当日买入，次日解锁
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[tuple[str, float]] = field(default_factory=list)  # (date, total equity)

    def position_value(self, prices: dict[str, float]) -> float:
        v = 0.0
        for code, h in self.holdings.items():
            p = prices.get(code)
            if p is None:
                continue
            v += p * h.shares
        return v

    def total_equity(self, prices: dict[str, float]) -> float:
        return self.cash + self.position_value(prices)

    def record_equity(self, date: str, prices: dict[str, float]) -> float:
        eq = self.total_equity(prices)
        self.equity_curve.append((date, eq))
        return eq

    # ---------- 下单 ----------

    def buy(self, code: str, row: dict, prev_close: float | None, target_amount: float) -> None:
        """按目标金额买入（金额不含成本）。受可买性、现金约束。"""
        if not can_buy(row, prev_close, code=code, name=row.get("name")):
            return
        price = float(row["close"])
        fill = self.costs.fill_price(price, is_buy=True)
        # 目标股数（整手），再被现金约束
        shares = shares_for_amount(fill, target_amount)
        if shares <= 0:
            return
        cost = self.costs.buy_cost(fill, shares)
        while shares > 0 and self.cash < fill * shares + cost:
            shares -= 100
            if shares <= 0:
                return
            cost = self.costs.buy_cost(fill, shares)
        if shares <= 0:
            return
        self.cash -= fill * shares + cost
        h = self.holdings.get(code)
        if h:
            total_cost = h.cost_price * h.shares + fill * shares
            h.shares += shares
            h.cost_price = total_cost / h.shares if h.shares > 0 else fill
        else:
            self.holdings[code] = Holding(code=code, shares=shares, cost_price=fill, buy_date=row.get("date", ""))
        self.t1_locked.add(code)
        self.trades.append(Trade(date=row.get("date", ""), code=code, side="buy", shares=shares, price=fill, cost=cost))

    def sell(self, code: str, row: dict, prev_close: float | None, target_shares: int | None = None) -> None:
        """卖出。target_shares=None 全平。受 T+1、可卖性约束。"""
        h = self.holdings.get(code)
        if not h or h.shares <= 0:
            return
        if not can_sell(code, row, prev_close, self.t1_locked, name=row.get("name")):
            return
        price = float(row["close"])
        fill = self.costs.fill_price(price, is_buy=False)
        qty = h.shares if target_shares is None else min(target_shares, h.shares)
        qty = round_lot(qty)
        if qty <= 0:
            return
        cost = self.costs.sell_cost(fill, qty)
        proceeds = fill * qty - cost
        self.cash += proceeds
        realized = (fill - h.cost_price) * qty - cost
        h.shares -= qty
        if h.shares <= 0:
            del self.holdings[code]
        self.trades.append(Trade(date=row.get("date", ""), code=code, side="sell", shares=qty, price=fill, cost=cost, pnl=realized))

    def end_of_day(self) -> None:
        """日终：T+1 锁定清空（次日开盘后这些仓位可卖）。"""
        self.t1_locked.clear()

    def mark_suspended(self, rows: dict[str, dict]) -> list[str]:
        """返回当前持仓中停牌的代码（无法卖出，仅按昨收计价）。供引擎日终标记。"""
        out: list[str] = []
        for code in list(self.holdings.keys()):
            row = rows.get(code)
            if row is not None and is_suspended(row):
                out.append(code)
        return out
