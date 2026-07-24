"""R2 信号流水线。"""

from __future__ import annotations

from quant.config import load_gates_config
from quant.domain.models import RegimeSnapshot
from quant.market.regime import detect_regime
from quant.io.quotes import build_stock_by_code
from quant.signals.entry import generate_buy_signals
from quant.signals.exit import generate_sell_signals
from quant.scoring.context import ScoreContext
from quant.trading.confirmation import apply_three_confirmations
from quant.trading.models import TradeSignal
from quant.store.state import codes_sold_today, holding_codes_bought_today


def _same_day_guards(
    buys: list[TradeSignal],
    sells: list[TradeSignal],
) -> tuple[list[TradeSignal], list[TradeSignal]]:
    sold = codes_sold_today()
    bought = holding_codes_bought_today()
    return (
        [s for s in buys if s.code not in sold],
        [s for s in sells if s.code not in bought],
    )


def generate_confirmed_signals(
    ctx: ScoreContext,
    *,
    mode: str,
    regime: RegimeSnapshot | None = None,
) -> tuple[list[TradeSignal], list[TradeSignal], list[TradeSignal], list[dict], RegimeSnapshot]:
    reg = regime or detect_regime(ctx.payload)
    stock_map = build_stock_by_code(ctx.payload)

    raw_buy = generate_buy_signals(ctx, regime=reg, stock_by_code=stock_map)
    raw_sell = generate_sell_signals(ctx, stock_by_code=stock_map)
    raw_buy, raw_sell = _same_day_guards(raw_buy, raw_sell)

    cfg = load_gates_config().get("confirmation") or {}
    if mode in (cfg.get("skip_count_modes") or []):
        audit = [{"状态": "盘前不计入持续确认", "可执行": False}]
        return raw_buy, raw_sell, [], audit, reg

    exec_buy, audit_buy = apply_three_confirmations(raw_buy, ctx, scope_action="买入")
    exec_sell, audit_sell = apply_three_confirmations(raw_sell, ctx, scope_action="卖出")
    return raw_buy, raw_sell, exec_sell + exec_buy, audit_buy + audit_sell, reg
