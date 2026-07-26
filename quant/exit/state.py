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
        self._states[code] = ExitState(code, entry_price, buy_date, entry_price)

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
