"""模拟撮合器（SimBroker）：现金 + 持仓 + T+1 锁定 + 成本。

每日流程：下单 → 次日开盘/收盘撮合 → 更新持仓与现金。
本实现采用「T 日收盘决策，T 日收盘成交」简化口径（与离线库日频对齐），
T+1 通过 t1_locked 集合实现：当日新买入次日才可卖。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from quant.backtest.costs import CostModel, DEFAULT_COSTS
from quant.backtest.tradability import (
    _fnum,
    can_buy,
    can_sell,
    cap_shares_by_adv,
    is_suspended,
    limit_state,
    price_at_limit,
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
    reason: str = ""  # 出场/调仓原因（如 atr_trailing / rebalance）


@dataclass
class SimBroker:
    cash: float
    costs: CostModel = field(default_factory=lambda: DEFAULT_COSTS)

    holdings: dict[str, Holding] = field(default_factory=dict)
    t1_locked: set[str] = field(default_factory=set)  # 当日买入，次日解锁
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[tuple[str, float]] = field(default_factory=list)  # (date, total equity)
    suspended_codes: set[str] = field(default_factory=set)  # 当日停牌持仓（报告用）

    def _mark_price(self, code: str, prices: dict[str, float], prev_closes: dict[str, float] | None) -> float | None:
        """取 T 日价；缺失/NaN/≤0（停牌/缺数据）→ 回退昨收，避免持仓按 0 计价导致净值跳动。"""
        f = _fnum(prices.get(code))
        if not math.isnan(f) and f > 0:
            return f
        ff = _fnum((prev_closes or {}).get(code))
        if not math.isnan(ff) and ff > 0:
            return ff
        return None

    def position_value(self, prices: dict[str, float], prev_closes: dict[str, float] | None = None) -> float:
        v = 0.0
        for code, h in self.holdings.items():
            p = self._mark_price(code, prices, prev_closes)
            if p is None:
                continue
            v += p * h.shares
        return v

    def total_equity(self, prices: dict[str, float], prev_closes: dict[str, float] | None = None) -> float:
        return self.cash + self.position_value(prices, prev_closes)

    def record_equity(self, date: str, prices: dict[str, float], prev_closes: dict[str, float] | None = None) -> float:
        eq = self.total_equity(prices, prev_closes)
        self.equity_curve.append((date, eq))
        return eq

    # ---------- 下单 ----------

    @staticmethod
    def _change_pct(row: dict, prev_close: float | None) -> float | None:
        """当日涨跌幅（%），供 microstructure 滑点用；缺数据 → None。"""
        if not prev_close or prev_close <= 0:
            return None
        c = _fnum(row.get("close"))
        if math.isnan(c):
            return None
        return (c - prev_close) / prev_close * 100.0

    def _slip_ctx(self, *, code: str, price: float, qty: int, change_pct: float | None,
                  adv_amount: float | None, volatility_pct: float | None):
        """构造 SlippageContext（回测侧用历史 ADV + 已实现波动），让 vol_scaled / ADV 冲击生效。"""
        from quant.execution.sim_rules import limit_pct
        from quant.execution.slippage import SlippageContext

        cfg = self.costs._sim()
        notional = price * max(qty, 100)
        adv = float(adv_amount or 0)
        part = (notional / adv) if adv > 0 else 0.0
        vol = volatility_pct if (volatility_pct and volatility_pct > 0) else 2.0
        return SlippageContext(
            volatility_pct=vol,
            amount=notional,
            day_change_pct=None,  # PIT: 不喂 T 日 close（strict T 开盘成交，T close 前视）；microstructure 退化到 vol/ADV 档
            limit_pct=limit_pct(code, cfg) if code else 9.9,
            adv_amount=adv,
            participation=part,
        )

    def buy(
        self,
        code: str,
        row: dict,
        prev_close: float | None,
        target_amount: float,
        *,
        ref_price: float | None = None,
        reason: str = "",
        adv_amount: float | None = None,
        volatility_pct: float | None = None,
    ) -> None:
        """按目标金额买入（金额不含成本）。受可买性、现金约束。

        ``ref_price`` 指定成交参考价（滑点前）；缺省用当日收盘。严格回测传开盘价，
        使成交价与调用方计算股数所用的价格同基准。

        ``adv_amount``（历史日均成交额，元）/ ``volatility_pct``（已实现波动 %）由引擎预计算
        传入，使 vol_scaled / ADV 冲击滑点在回测生效；缺省回退固定档（兼容旧测试）。
        """
        if not can_buy(row, prev_close, code=code, name=row.get("name")):
            return
        price = float(ref_price) if ref_price and ref_price > 0 else float(row["close"])
        # strict（ref_price=T 开盘）模式：开盘价已达涨停 → 实盘买不进（避免用收盘封板口径误判）
        if ref_price and ref_price > 0 and price_at_limit(price, prev_close, code=code, name=row.get("name")) == "up":
            return
        try:
            day_amt = float(row.get("amount") or 0)
        except (TypeError, ValueError):
            day_amt = 0.0
        # ADV 封顶：优先用历史日均成交额（PIT），缺失才回退当日额
        adv = adv_amount if adv_amount and adv_amount > 0 else day_amt
        shares = shares_for_amount(price, target_amount)
        _part_rate = float(getattr(self.costs._sim(), "participation_rate", 0.1) or 0.1)
        shares = cap_shares_by_adv(shares, price=price, day_amount=adv, max_pct=_part_rate)
        if shares <= 0:
            return
        change_pct = self._change_pct(row, prev_close)
        # 滑点依赖股数（notional 项），故先定股数再算 fill/cost（与 calc_buy_cost 同口径）
        ctx = self._slip_ctx(code=code, price=price, qty=shares, change_pct=change_pct,
                             adv_amount=adv, volatility_pct=volatility_pct)
        fill = self.costs.fill_price(price, is_buy=True, code=code, quantity=shares, ctx=ctx)
        cost = self.costs.buy_cost(fill, shares, code=code)
        while shares > 0 and self.cash < fill * shares + cost:
            shares -= 100
            if shares <= 0:
                return
            ctx = self._slip_ctx(code=code, price=price, qty=shares, change_pct=change_pct,
                                 adv_amount=adv, volatility_pct=volatility_pct)
            fill = self.costs.fill_price(price, is_buy=True, code=code, quantity=shares, ctx=ctx)
            cost = self.costs.buy_cost(fill, shares, code=code)
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
        self.trades.append(
            Trade(
                date=row.get("date", ""),
                code=code,
                side="buy",
                shares=shares,
                price=fill,
                cost=cost,
                reason=reason or "rebalance",
            )
        )

    def sell(
        self,
        code: str,
        row: dict,
        prev_close: float | None,
        target_shares: int | None = None,
        *,
        ref_price: float | None = None,
        reason: str = "",
        adv_amount: float | None = None,
        volatility_pct: float | None = None,
    ) -> None:
        """卖出。target_shares=None 全平。受 T+1、可卖性约束。

        ``ref_price`` 同 ``buy``：指定成交参考价（滑点前），缺省用当日收盘。
        ``adv_amount`` / ``volatility_pct`` 同 ``buy``，供回测滑点冲击计算。
        """
        h = self.holdings.get(code)
        if not h or h.shares <= 0:
            return
        if not can_sell(code, row, prev_close, self.t1_locked, name=row.get("name")):
            return
        price = float(ref_price) if ref_price and ref_price > 0 else float(row["close"])
        # strict（ref_price=T 开盘）模式：开盘价已达跌停 → 实盘卖不出
        if ref_price and ref_price > 0 and price_at_limit(price, prev_close, code=code, name=row.get("name")) == "down":
            return
        qty = h.shares if target_shares is None else min(target_shares, h.shares)
        qty = round_lot(qty)
        if qty <= 0:
            return
        change_pct = self._change_pct(row, prev_close)
        try:
            day_amt = float(row.get("amount") or 0)
        except (TypeError, ValueError):
            day_amt = 0.0
        adv = adv_amount if adv_amount and adv_amount > 0 else day_amt
        # 滑点依赖股数；先用未滑点价定 qty，再算 fill/cost（与 calc_sell_proceeds 同口径）
        ctx = self._slip_ctx(code=code, price=price, qty=qty, change_pct=change_pct,
                             adv_amount=adv, volatility_pct=volatility_pct)
        fill = self.costs.fill_price(price, is_buy=False, code=code, quantity=qty, ctx=ctx)
        cost = self.costs.sell_cost(fill, qty, code=code)
        proceeds = fill * qty - cost
        self.cash += proceeds
        realized = (fill - h.cost_price) * qty - cost
        h.shares -= qty
        if h.shares <= 0:
            del self.holdings[code]
        self.trades.append(
            Trade(
                date=row.get("date", ""),
                code=code,
                side="sell",
                shares=qty,
                price=fill,
                cost=cost,
                pnl=realized,
                reason=reason or "rebalance",
            )
        )

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
