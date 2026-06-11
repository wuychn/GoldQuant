"""信号流水线：原始信号 → 同日防翻转 → 持续确认 → 可执行信号。"""

from __future__ import annotations

from quant.config import load_gates_config
from quant.scoring.context import ScoreContext
from quant.signals.buy import generate_buy_signals
from quant.signals.confirmation import apply_three_confirmations
from quant.signals.models import TradeSignal
from quant.signals.sell import generate_sell_signals
from quant.store.state import codes_sold_today, holding_codes_bought_today


def _apply_same_day_guards(
    raw_buy: list[TradeSignal],
    raw_sell: list[TradeSignal],
) -> tuple[list[TradeSignal], list[TradeSignal]]:
    """T+1 与当日翻转：当日已买不卖、当日已卖不买。"""
    sold_today = codes_sold_today()
    bought_today = holding_codes_bought_today()
    buy = [s for s in raw_buy if s.code not in sold_today]
    sell = [s for s in raw_sell if s.code not in bought_today]
    return buy, sell


def generate_confirmed_signals(
    ctx: ScoreContext,
    *,
    mode: str,
) -> tuple[list[TradeSignal], list[TradeSignal], list[TradeSignal], list[dict]]:
    """返回 (raw_buy, raw_sell, executable_all, confirmation_audit)。"""
    raw_buy = generate_buy_signals(ctx, mode=mode)
    raw_sell = generate_sell_signals(ctx)
    raw_buy, raw_sell = _apply_same_day_guards(raw_buy, raw_sell)

    cfg = load_gates_config().get("confirmation") or {}
    skip_modes = cfg.get("skip_count_modes") or []
    if mode in skip_modes:
        audit = [
            {
                "状态": "盘前不计入持续确认",
                "说明": "首次计数自09:37盘中调度起算",
                "可执行": False,
            }
        ]
        return raw_buy, raw_sell, [], audit

    exec_buy, audit_buy = apply_three_confirmations(raw_buy, ctx, scope_action="买入")
    exec_sell, audit_sell = apply_three_confirmations(raw_sell, ctx, scope_action="卖出")
    audit = audit_buy + audit_sell
    return raw_buy, raw_sell, exec_sell + exec_buy, audit
