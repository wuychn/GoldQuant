"""统一撮合内核：executor 与 SimBroker 共用。"""

from __future__ import annotations

from dataclasses import dataclass

from quant.execution.sim_rules import (
    TradeSimConfig,
    at_limit_up_down,
    calc_commission,
    calc_stamp_tax,
    calc_transfer_fee,
    load_trade_sim_config,
)
from quant.execution.slippage import slip_price_with_context
from quant.signals.models import TradeSignal
from quant.signals.sell_policy import sell_requires_late_session
from quant.trading_hours import is_a_share_continuous_auction_window, is_late_session_for_trend_sell


@dataclass
class MatchResult:
    ok: bool
    fill_price: float = 0.0
    quantity: int = 0
    commission: float = 0.0
    stamp_tax: float = 0.0
    transfer_fee: float = 0.0
    net_cash_delta: float = 0.0
    pnl: float = 0.0
    rejected: str = ""


def _cap_qty_by_participation(
    qty: int,
    price: float,
    stock: dict | None,
    sim: TradeSimConfig,
) -> int:
    """部分成交：按 ADV×participation_rate 封顶可成交手数（100 股整数倍）。"""
    if not getattr(sim, "partial_fill_enabled", False):
        return qty
    if price <= 0 or qty < 100:
        return qty
    from quant.pool.liquidity import avg_daily_amount_yi

    adv_yi = avg_daily_amount_yi(stock) if stock else None
    if not adv_yi or adv_yi <= 0:
        return qty
    max_notional = adv_yi * 1e8 * float(getattr(sim, "participation_rate", 0.1) or 0.1)
    capped = int(max_notional / price / 100) * 100
    return max(0, min(qty, capped))


def _time_ok(*, enforce_hours: bool) -> bool:
    if not enforce_hours:
        return True
    return is_a_share_continuous_auction_window()


def match_sell_order(
    signal: TradeSignal,
    stock: dict | None,
    *,
    holding_qty: int,
    buy_price: float,
    bought_today: bool,
    enforce_hours: bool = False,
    enforce_late_session: bool = True,
    sim: TradeSimConfig | None = None,
) -> MatchResult:
    sim = sim or load_trade_sim_config()
    if not _time_ok(enforce_hours=enforce_hours):
        return MatchResult(False, rejected="非连续竞价时段")
    if bought_today:
        return MatchResult(False, rejected="T+1")
    if holding_qty < 100:
        return MatchResult(False, rejected="无持仓")
    if enforce_late_session and sell_requires_late_session(signal, stock, signal.code):
        if not is_late_session_for_trend_sell():
            return MatchResult(False, rejected="等14:30后执行")
    if at_limit_up_down(stock, signal.code, side="sell", cfg=sim):
        return MatchResult(False, rejected="跌停")
    qty = min(signal.quantity, holding_qty)
    qty = _cap_qty_by_participation(qty, signal.price, stock, sim)
    if qty < 100:
        return MatchResult(False, rejected="流动性不足/部分成交为0")
    fill = slip_price_with_context(
        signal.price, side="sell", cfg=sim, stock=stock, code=signal.code, quantity=qty
    )
    amount = fill * qty
    comm = calc_commission(amount, sim)
    tax = calc_stamp_tax(amount, side="sell", cfg=sim)
    xfer = calc_transfer_fee(amount, signal.code, sim)
    net = amount - comm - tax - xfer
    pnl = (fill - buy_price) * qty - comm - tax - xfer
    return MatchResult(
        True,
        fill_price=fill,
        quantity=qty,
        commission=comm,
        stamp_tax=tax,
        transfer_fee=xfer,
        net_cash_delta=net,
        pnl=pnl,
    )


def match_buy_order(
    signal: TradeSignal,
    stock: dict | None,
    *,
    cash: float,
    sold_today: bool,
    already_held: bool,
    enforce_hours: bool = False,
    sim: TradeSimConfig | None = None,
) -> MatchResult:
    sim = sim or load_trade_sim_config()
    if not _time_ok(enforce_hours=enforce_hours):
        return MatchResult(False, rejected="非连续竞价时段")
    if sold_today:
        return MatchResult(False, rejected="当日已卖不回补")
    if already_held:
        return MatchResult(False, rejected="已持仓")
    if at_limit_up_down(stock, signal.code, side="buy", cfg=sim):
        return MatchResult(False, rejected="涨停")
    qty = _cap_qty_by_participation(signal.quantity, signal.price, stock, sim)
    if qty < 100:
        return MatchResult(False, rejected="流动性不足/部分成交为0")
    fill = slip_price_with_context(
        signal.price,
        side="buy",
        cfg=sim,
        stock=stock,
        code=signal.code,
        quantity=qty,
    )
    amount = fill * qty
    comm = calc_commission(amount, sim)
    xfer = calc_transfer_fee(amount, signal.code, sim)
    total = amount + comm + xfer
    if total > cash + 1e-6:
        # 资金不足时尝试缩量到可买手数
        afford = int(cash / max(fill * 1.002, 1e-6) / 100) * 100
        afford = min(afford, qty)
        if afford < 100:
            return MatchResult(False, rejected="资金不足")
        qty = afford
        amount = fill * qty
        comm = calc_commission(amount, sim)
        xfer = calc_transfer_fee(amount, signal.code, sim)
        total = amount + comm + xfer
        if total > cash + 1e-6:
            return MatchResult(False, rejected="资金不足")
    return MatchResult(
        True,
        fill_price=fill,
        quantity=qty,
        commission=comm,
        transfer_fee=xfer,
        net_cash_delta=-total,
    )
