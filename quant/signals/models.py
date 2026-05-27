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
    # 主升浪买点/卖点子类型，三确认链 key 的一部分
    signal_kind: str = ""
    confirmation_stage: int = 0
