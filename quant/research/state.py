"""组合状态抽象：文件 state / 内存 SimBroker 共用接口。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class PortfolioState(Protocol):
    """回测、纸交易、实盘模拟的统一持仓/自选接口。"""

    def get_cash(self) -> float: ...
    def get_holdings(self) -> list[dict]: ...
    def get_watchlist(self) -> list[dict]: ...
    def get_observe_pool(self) -> list[dict]: ...
    def set_watchlist(self, rows: list[dict]) -> None: ...
    def set_observe_pool(self, rows: list[dict]) -> None: ...
    def codes_sold_today(self) -> set[str]: ...
    def codes_bought_today(self) -> set[str]: ...
    def get_total_assets(self, payload: dict | None = None) -> float: ...


@dataclass
class MemoryPortfolioState:
    """内存组合状态，供端到端回测使用。"""

    cash: float
    holdings: dict[str, dict[str, Any]] = field(default_factory=dict)
    watchlist: list[dict] = field(default_factory=list)
    observe_pool: list[dict] = field(default_factory=list)
    sold_today: set[str] = field(default_factory=set)
    bought_today: set[str] = field(default_factory=set)
    stoploss_codes: dict[str, str] = field(default_factory=dict)
    daily_realized_pnl: float = 0.0
    current_date: str = ""

    def reset_daily(self, date_str: str) -> None:
        self.current_date = date_str
        self.sold_today.clear()
        self.bought_today.clear()
        self.daily_realized_pnl = 0.0

    def get_cash(self) -> float:
        return self.cash

    def get_holdings(self) -> list[dict]:
        return list(self.holdings.values())

    def get_watchlist(self) -> list[dict]:
        return list(self.watchlist)

    def get_observe_pool(self) -> list[dict]:
        return list(self.observe_pool)

    def set_watchlist(self, rows: list[dict]) -> None:
        self.watchlist = list(rows)

    def set_observe_pool(self, rows: list[dict]) -> None:
        self.observe_pool = list(rows)

    def codes_sold_today(self) -> set[str]:
        return set(self.sold_today)

    def codes_bought_today(self) -> set[str]:
        return set(self.bought_today)

    def holdings_market_value(self, price_map: dict[str, float]) -> float:
        mv = 0.0
        for code, h in self.holdings.items():
            px = price_map.get(code) or float(h.get("买入价", 0) or 0)
            mv += px * int(h.get("持仓股数", 0) or 0)
        return mv

    def get_total_assets(self, payload: dict | None = None) -> float:
        price_map: dict[str, float] = {}
        if payload:
            from quant.scoring.tech_indicators import quote_last_price

            for key in ("持仓股", "自选股"):
                for row in payload.get(key) or []:
                    if not isinstance(row, dict):
                        continue
                    code = str(row.get("股票代码", "")).strip()
                    px = quote_last_price(row)
                    if code and px:
                        price_map[code] = px
        return self.cash + self.holdings_market_value(price_map)
