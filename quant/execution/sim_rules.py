"""A 股模拟撮合共用规则：佣金、印花税、过户费、滑点、涨跌停。"""

from __future__ import annotations

from dataclasses import dataclass

from quant.config import load_gates_config
from quant.data.quote import quote_change_pct


@dataclass
class TradeSimConfig:
    commission_rate: float = 0.0001
    min_commission: float = 5.0
    stamp_tax_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    slippage_pct: float = 0.001
    slippage_model: str = "fixed"  # fixed | vol_scaled | microstructure | sqrt_law
    slippage_sqrt_k: float = 0.5
    slippage_max_pct: float = 0.005
    slippage_sqrt_max_pct: float = 0.02
    partial_fill_enabled: bool = False
    participation_rate: float = 0.1
    main_limit_pct: float = 9.9
    gem_limit_pct: float = 19.9


def load_trade_sim_config() -> TradeSimConfig:
    import os

    raw = (load_gates_config().get("trading") or {}).get("simulation") or {}
    if not isinstance(raw, dict):
        raw = {}
    slip_override = os.environ.get("GOLDQUANT_BACKTEST_SLIP")
    slippage_pct = float(slip_override) if slip_override else float(raw.get("slippage_pct", 0.001))
    return TradeSimConfig(
        commission_rate=float(raw.get("commission_rate", 0.0001)),
        min_commission=float(raw.get("min_commission", 5.0)),
        stamp_tax_rate=float(raw.get("stamp_tax_rate", 0.0005)),
        transfer_fee_rate=float(raw.get("transfer_fee_rate", 0.00001)),
        slippage_pct=slippage_pct,
        slippage_model=str(raw.get("slippage_model", "fixed")),
        slippage_sqrt_k=float(raw.get("slippage_sqrt_k", 0.5)),
        slippage_max_pct=float(raw.get("slippage_max_pct", 0.005)),
        slippage_sqrt_max_pct=float(raw.get("slippage_sqrt_max_pct", 0.02)),
        partial_fill_enabled=bool(raw.get("partial_fill_enabled", False)),
        participation_rate=float(raw.get("participation_rate", 0.1)),
        main_limit_pct=float(raw.get("main_limit_pct", 9.9)),
        gem_limit_pct=float(raw.get("gem_limit_pct", 19.9)),
    )


def limit_pct(code: str, cfg: TradeSimConfig, name: str | None = None) -> float:
    """涨跌停比例（百分点）。

    单一来源 = ``backtest.tradability._limit_pct``（board + ST 感知）：
    主板 10 / 创业板·科创 20 / 北交 30 / ST 5。旧实现仅 9.9/19.9，漏判 ST 5% 与北交 30%，
    致实盘对 ST 与北交股的涨跌停判定偏松；现与回测 ``tradability`` 完全一致。
    """
    from quant.backtest.tradability import _limit_pct as _tier

    return _tier(code, name) * 100.0


def at_limit_up_down(stock: dict | None, code: str, *, side: str, cfg: TradeSimConfig) -> bool:
    if not stock:
        return False
    chg = quote_change_pct(stock)
    if chg is None:
        return False
    name = stock.get("股票名称") or stock.get("name")
    lim = limit_pct(code, cfg, name)
    if side == "buy" and chg >= lim - 0.05:
        return True
    if side == "sell" and chg <= -lim + 0.05:
        return True
    return False


def slip_price(
    price: float,
    *,
    side: str,
    cfg: TradeSimConfig,
    stock: dict | None = None,
    code: str = "",
    quantity: int = 0,
) -> float:
    from quant.execution.slippage import slip_price_with_context

    return slip_price_with_context(
        price, side=side, cfg=cfg, stock=stock, code=code, quantity=quantity
    )


def calc_commission(amount: float, cfg: TradeSimConfig) -> float:
    if amount <= 0:
        return 0.0
    return round(max(cfg.min_commission, amount * cfg.commission_rate), 2)


def calc_transfer_fee(amount: float, code: str, cfg: TradeSimConfig) -> float:
    if amount <= 0:
        return 0.0
    if str(code).strip().startswith("6"):
        return round(amount * cfg.transfer_fee_rate, 2)
    return 0.0


def calc_stamp_tax(amount: float, *, side: str, cfg: TradeSimConfig) -> float:
    if side != "sell" or amount <= 0:
        return 0.0
    return round(amount * cfg.stamp_tax_rate, 2)


@dataclass
class BuyCostBreakdown:
    fill_price: float
    quantity: int
    amount: float
    commission: float
    transfer_fee: float
    total: float


@dataclass
class SellProceedsBreakdown:
    fill_price: float
    quantity: int
    amount: float
    commission: float
    stamp_tax: float
    transfer_fee: float
    net_proceeds: float
    pnl: float


def calc_buy_cost(
    signal_price: float,
    quantity: int,
    code: str,
    cfg: TradeSimConfig,
    stock: dict | None = None,
) -> BuyCostBreakdown:
    fill = slip_price(
        signal_price, side="buy", cfg=cfg, stock=stock, code=code, quantity=quantity
    )
    amount = fill * quantity
    comm = calc_commission(amount, cfg)
    xfer = calc_transfer_fee(amount, code, cfg)
    total = amount + comm + xfer
    return BuyCostBreakdown(fill, quantity, amount, comm, xfer, total)


def calc_sell_proceeds(
    signal_price: float,
    quantity: int,
    code: str,
    buy_price: float,
    cfg: TradeSimConfig,
    stock: dict | None = None,
) -> SellProceedsBreakdown:
    fill = slip_price(
        signal_price, side="sell", cfg=cfg, stock=stock, code=code, quantity=quantity
    )
    amount = fill * quantity
    comm = calc_commission(amount, cfg)
    tax = calc_stamp_tax(amount, side="sell", cfg=cfg)
    xfer = calc_transfer_fee(amount, code, cfg)
    net = amount - comm - tax - xfer
    pnl = (fill - buy_price) * quantity - comm - tax - xfer
    return SellProceedsBreakdown(fill, quantity, amount, comm, tax, xfer, net, pnl)
