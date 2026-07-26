"""成交成本模型：佣金 + 印花税 + 过户费 + 滑点。

A股现行（2024）：
- 佣金：万分之 2.5（双边，最低 5 元）
- 印花税：千分之 0.5（仅卖出）
- 过户费：万分之 0.1（双边，沪市）
- 滑点：按成交价比例，默认万分之 5
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    commission_bps: float = 0.00025  # 万 2.5
    min_commission: float = 5.0
    stamp_bps: float = 0.0005  # 千 0.5（卖出）
    transfer_fee_bps: float = 0.00001  # 万 0.1
    slippage_bps: float = 0.0005  # 万 5

    def buy_cost(self, price: float, shares: int) -> float:
        amount = price * shares
        comm = max(amount * self.commission_bps, self.min_commission)
        transfer = amount * self.transfer_fee_bps
        slip = amount * self.slippage_bps
        return comm + transfer + slip

    def sell_cost(self, price: float, shares: int) -> float:
        amount = price * shares
        comm = max(amount * self.commission_bps, self.min_commission)
        stamp = amount * self.stamp_bps
        transfer = amount * self.transfer_fee_bps
        slip = amount * self.slippage_bps
        return comm + stamp + transfer + slip

    def fill_price(self, price: float, is_buy: bool) -> float:
        """滑点后成交价：买高卖低。"""
        return price * (1 + self.slippage_bps) if is_buy else price * (1 - self.slippage_bps)


DEFAULT_COSTS = CostModel()
