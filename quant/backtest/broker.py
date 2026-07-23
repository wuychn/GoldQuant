"""模拟撮合：手续费、印花税、滑点、涨跌停、T+1。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from quant.execution.sim_rules import (
    at_limit_up_down,
    calc_buy_cost,
    calc_sell_proceeds,
    load_trade_sim_config,
)
from quant.scoring.tech_indicators import quote_last_price
from quant.trading.models import TradeSignal

# 独立收盘价取价器：(股票代码, YYYY-MM-DD) -> 收盘价 或 None。
# 用于 mark_to_market：持仓掉出当日自选/持仓快照时，按真实收盘价估值，而非回退买入价。
PriceProvider = Callable[[str, str], "float | None"]


@dataclass
class BrokerConfig:
    initial_cash: float = 100_000.0


@dataclass
class FillRecord:
    date: str
    signal: TradeSignal
    fill_price: float
    quantity: int
    commission: float
    stamp_tax: float
    transfer_fee: float = 0.0
    pnl: float = 0.0
    rejected: str = ""


@dataclass
class SimBroker:
    cfg: BrokerConfig = field(default_factory=BrokerConfig)
    cash: float = 0.0
    holdings: dict[str, dict[str, Any]] = field(default_factory=dict)
    trades: list[FillRecord] = field(default_factory=list)
    sold_today: set[str] = field(default_factory=set)
    bought_today: set[str] = field(default_factory=set)
    current_date: str = ""
    equity_curve: list[dict[str, float]] = field(default_factory=list)
    price_provider: PriceProvider | None = None

    def __post_init__(self) -> None:
        if self.cash <= 0:
            self.cash = self.cfg.initial_cash

    def reset_daily(self, date_str: str) -> None:
        self.current_date = date_str
        self.sold_today.clear()
        self.bought_today.clear()

    @property
    def _sim(self):
        return load_trade_sim_config()

    def holdings_rows(self) -> list[dict]:
        return list(self.holdings.values())

    def try_sell(self, signal: TradeSignal, stock: dict) -> FillRecord | None:
        sim = self._sim
        code = signal.code
        if code in self.bought_today:
            rec = FillRecord(self.current_date, signal, 0, 0, 0, 0, rejected="T+1")
            self.trades.append(rec)
            return None
        h = self.holdings.get(code)
        if not h:
            return None
        if at_limit_up_down(stock, code, side="sell", cfg=sim):
            rec = FillRecord(self.current_date, signal, 0, 0, 0, 0, rejected="跌停")
            self.trades.append(rec)
            return None
        qty = min(signal.quantity, int(h.get("持仓股数", 0) or 0))
        if qty < 100:
            return None
        buy_price = float(h.get("买入价", 0) or 0)
        proceeds = calc_sell_proceeds(signal.price, qty, code, buy_price, sim)
        self.cash += proceeds.net_proceeds
        remain = int(h.get("持仓股数", 0)) - qty
        if remain >= 100:
            h["持仓股数"] = remain
        else:
            del self.holdings[code]
        self.sold_today.add(code)
        rec = FillRecord(
            self.current_date,
            signal,
            proceeds.fill_price,
            qty,
            proceeds.commission,
            proceeds.stamp_tax,
            proceeds.transfer_fee,
            pnl=proceeds.pnl,
        )
        self.trades.append(rec)
        return rec

    def try_buy(self, signal: TradeSignal, stock: dict) -> FillRecord | None:
        sim = self._sim
        code = signal.code
        if code in self.sold_today or code in self.holdings:
            return None
        if at_limit_up_down(stock, code, side="buy", cfg=sim):
            rec = FillRecord(self.current_date, signal, 0, 0, 0, 0, rejected="涨停")
            self.trades.append(rec)
            return None
        cost = calc_buy_cost(signal.price, signal.quantity, code, sim)
        if cost.total > self.cash + 1e-6:
            rec = FillRecord(self.current_date, signal, 0, 0, 0, 0, rejected="资金不足")
            self.trades.append(rec)
            return None
        self.cash -= cost.total
        self.holdings[code] = {
            "股票代码": code,
            "股票名称": signal.name,
            "买入价": cost.fill_price,
            "买入时间": f"{self.current_date} 15:00:00",
            "买入类型": signal.signal_kind or "",
            "买入原因": signal.reason[:120],
            "战法": signal.strategy,
            "持仓股数": signal.quantity,
            "买入日期": self.current_date,
        }
        self.bought_today.add(code)
        rec = FillRecord(
            self.current_date,
            signal,
            cost.fill_price,
            signal.quantity,
            cost.commission,
            0.0,
            cost.transfer_fee,
        )
        self.trades.append(rec)
        return rec

    def mark_to_market(self, payload: dict) -> None:
        mv = 0.0
        price_map: dict[str, float] = {}
        for rows_key in ("持仓股", "自选股"):
            for row in payload.get(rows_key) or []:
                if not isinstance(row, dict):
                    continue
                code = str(row.get("股票代码", "")).strip()
                px = quote_last_price(row)
                if code and px:
                    price_map[code] = px
        for code, h in self.holdings.items():
            # 估值优先级：当日快照最新价 → 独立收盘价（掉出自选时）→ 买入价（兜底）
            px = price_map.get(code)
            if not px and self.price_provider:
                px = self.price_provider(code, self.current_date)
            if not px:
                px = float(h.get("买入价", 0) or 0)
            mv += px * int(h.get("持仓股数", 0) or 0)
        equity = self.cash + mv
        self.equity_curve.append(
            {"date": self.current_date, "cash": self.cash, "mv": mv, "equity": equity}
        )
