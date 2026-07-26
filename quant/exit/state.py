"""持仓出场状态：跟踪入场后最高收盘价。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExitState:
    code: str
    entry_price: float
    buy_date: str
    highest_close: float

    def update(self, close: float) -> None:
        if close > self.highest_close:
            self.highest_close = close


class ExitTracker:
    """按 code 维护出场状态。"""

    def __init__(self) -> None:
        self._states: dict[str, ExitState] = {}

    def open(self, code: str, entry_price: float, buy_date: str) -> None:
        """新建仓：重置状态（highest_close 从入场价起算）。

        已有状态时会被覆盖，调用方须确保这是真正的新建仓；加仓请用 ``upsert``。
        """
        self._states[code] = ExitState(code, entry_price, buy_date, entry_price)

    def upsert(self, code: str, entry_price: float, buy_date: str) -> None:
        """新建仓则 open；已持仓（加仓）则只更新成本价，保留 highest_close。

        加仓不能重置 highest_close：否则已积累的涨幅被抹掉，ATR 跟踪止损的
        止损线会随成本价回落，跟踪止损形同失效。
        """
        s = self._states.get(code)
        if s is None:
            self.open(code, entry_price, buy_date)
            return
        s.entry_price = entry_price
        # highest_close 取历史最高与新成本价的较大者，绝不回退
        if entry_price > s.highest_close:
            s.highest_close = entry_price

    def update(self, code: str, close: float) -> None:
        s = self._states.get(code)
        if s:
            s.update(close)

    def get(self, code: str) -> ExitState | None:
        return self._states.get(code)

    def close(self, code: str) -> None:
        self._states.pop(code, None)

    def all(self) -> dict[str, ExitState]:
        return dict(self._states)
