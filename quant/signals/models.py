"""交易信号模型。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TradeSignal:
    action: str
    code: str
    name: str
    price: float
    quantity: int
    strategy: str
    reason: str
    sell_type: str = ""
