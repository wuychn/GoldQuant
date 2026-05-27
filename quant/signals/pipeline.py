"""信号流水线：原始信号 → 三确认 → 可执行信号。"""

from __future__ import annotations

from quant.scoring.context import ScoreContext
from quant.signals.buy import generate_buy_signals
from quant.signals.confirmation import apply_three_confirmations
from quant.signals.models import TradeSignal
from quant.signals.sell import generate_sell_signals


def generate_confirmed_signals(
    ctx: ScoreContext,
    *,
    mode: str,
) -> tuple[list[TradeSignal], list[TradeSignal], list[TradeSignal], list[dict]]:
    """返回 (raw_buy, raw_sell, executable_all, confirmation_audit)。"""
    raw_buy = generate_buy_signals(ctx, mode=mode)
    raw_sell = generate_sell_signals(ctx)
    exec_buy, audit_buy = apply_three_confirmations(raw_buy, ctx)
    exec_sell, audit_sell = apply_three_confirmations(raw_sell, ctx)
    audit = audit_buy + audit_sell
    return raw_buy, raw_sell, exec_sell + exec_buy, audit
