"""A 股模拟撮合共用规则：佣金、印花税、过户费、滑点、涨跌停。"""

from __future__ import annotations

from dataclasses import dataclass

from quant.config import load_gates_config
from quant.scoring.tech_indicators import quote_change_pct


@dataclass
class TradeSimConfig:
    commission_rate: float = 0.0001
    min_commission: float = 5.0
    stamp_tax_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    slippage_pct: float = 0.001
    main_limit_pct: float = 9.9
    gem_limit_pct: float = 19.9


def load_trade_sim_config() -> TradeSimConfig:
    raw = (load_gates_config().get("trading") or {}).get("simulation") or {}
    if not isinstance(raw, dict):
        raw = {}
    return TradeSimConfig(
        commission_rate=float(raw.get("commission_rate", 0.0001)),
        min_commission=float(raw.get("min_commission", 5.0)),
        stamp_tax_rate=float(raw.get("stamp_tax_rate", 0.0005)),
        transfer_fee_rate=float(raw.get("transfer_fee_rate", 0.00001)),
        slippage_pct=float(raw.get("slippage_pct", 0.001)),
        main_limit_pct=float(raw.get("main_limit_pct", 9.9)),
        gem_limit_pct=float(raw.get("gem_limit_pct", 19.9)),
    )


def limit_pct(code: str, cfg: TradeSimConfig) -> float:
    c = str(code).strip()
    if c.startswith(("30", "68")):
        return cfg.gem_limit_pct
    return cfg.main_limit_pct


def at_limit_up_down(stock: dict | None, code: str, *, side: str, cfg: TradeSimConfig) -> bool:
    if not stock:
        return False
    chg = quote_change_pct(stock)
    if chg is None:
        return False
    lim = limit_pct(code, cfg)
    if side == "buy" and chg >= lim - 0.05:
        return True
    if side == "sell" and chg <= -lim + 0.05:
        return True
    return False


def slip_price(price: float, *, side: str, cfg: TradeSimConfig) -> float:
    slip = cfg.slippage_pct
    if side == "buy":
        return round(price * (1 + slip), 4)
    return round(price * (1 - slip), 4)


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
) -> BuyCostBreakdown:
    fill = slip_price(signal_price, side="buy", cfg=cfg)
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
) -> SellProceedsBreakdown:
    fill = slip_price(signal_price, side="sell", cfg=cfg)
    amount = fill * quantity
    comm = calc_commission(amount, cfg)
    tax = calc_stamp_tax(amount, side="sell", cfg=cfg)
    xfer = calc_transfer_fee(amount, code, cfg)
    net = amount - comm - tax - xfer
    pnl = (fill - buy_price) * quantity - comm - tax - xfer
    return SellProceedsBreakdown(fill, quantity, amount, comm, tax, xfer, net, pnl)
