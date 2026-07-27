"""统一成交成本模型：backtest 与 live 共用同一来源。

单一配置来源 = ``execution.sim_rules.TradeSimConfig``（读 ``quant.yml gates.trading.simulation``），
佣金万一/最低 5、卖出印花税千 0.5、沪市过户费万 0.1、滑点 vol_scaled。

关键修复：
- 滑点仅在 ``fill_price`` 应用**一次**（旧 ``CostModel`` 在 fill_price 抬价后又于 buy_cost/sell_cost
  重复计滑点，回测系统性高估成本）。
- 佣金/滑点/过户费口径与实盘 ``calc_buy_cost``/``calc_sell_proceeds`` 完全一致（消除
  回测万2.5佣金 vs 实盘万1 的分歧）。
"""

from __future__ import annotations

from dataclasses import dataclass

from quant.execution.sim_rules import (
    TradeSimConfig,
    calc_commission,
    calc_stamp_tax,
    calc_transfer_fee,
    load_trade_sim_config,
)
from quant.execution.slippage import slip_price_with_context


@dataclass
class CostModel:
    """成本模型：委托 sim_rules 的同一套计算，保证回测=实盘。

    ``sim=None`` 时惰性加载配置（避免模块导入期读 yaml）。
    """

    sim: TradeSimConfig | None = None

    def _sim(self) -> TradeSimConfig:
        return self.sim or load_trade_sim_config()

    def fill_price(
        self,
        price: float,
        is_buy: bool,
        *,
        code: str = "",
        stock: dict | None = None,
        quantity: int = 0,
    ) -> float:
        """滑点后成交价（买高卖低），滑点仅在此应用一次。"""
        return slip_price_with_context(
            price,
            side="buy" if is_buy else "sell",
            cfg=self._sim(),
            stock=stock,
            code=code,
            quantity=quantity,
        )

    def buy_cost(self, fill_price: float, shares: int, *, code: str = "") -> float:
        """买入成本（佣金 + 过户费；不含滑点，滑点已计入 fill_price）。"""
        amount = fill_price * shares
        return calc_commission(amount, self._sim()) + calc_transfer_fee(amount, code, self._sim())

    def sell_cost(self, fill_price: float, shares: int, *, code: str = "") -> float:
        """卖出成本（佣金 + 印花税 + 过户费；不含滑点）。"""
        amount = fill_price * shares
        return (
            calc_commission(amount, self._sim())
            + calc_stamp_tax(amount, side="sell", cfg=self._sim())
            + calc_transfer_fee(amount, code, self._sim())
        )


DEFAULT_COSTS = CostModel()
