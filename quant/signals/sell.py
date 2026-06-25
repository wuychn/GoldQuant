"""卖出原始信号：止损（紧急）+ 趋势破位（14:30 后）。"""

from __future__ import annotations

from quant.config import load_gates_config
from quant.constants import (
    BUY_KIND_PULLBACK,
    SELL_KIND_MA5_BREAK,
    SELL_KIND_TREND_ERODE,
    STRATEGY_NAME,
)
from quant.scoring.context import ScoreContext
from quant.signals.models import TradeSignal
from quant.store.state import get_holdings
from quant.scoring.tech_indicators import mas_from_stock, quote_last_price
from quant.signals.sell_policy import stop_loss_triggers_sell
from quant.strategy.main_wave import detect_sell_setup
from quant.strategy.trend import effective_ma20_break, trend_allows_ascent_sell


def _price(stock: dict) -> float | None:
    return quote_last_price(stock)


def _position_buy_kind(holding: dict) -> str:
    kind = str(holding.get("买入类型") or "").strip()
    if kind:
        return kind
    reason = str(holding.get("买入原因") or "")
    if "回调企稳" in reason:
        return BUY_KIND_PULLBACK
    return ""


def verify_sell_signal_still_valid(
    code: str,
    ctx: ScoreContext,
    *,
    signal_kind: str = "",
) -> bool:
    """持续确认成交前再验：持仓仍满足同类型卖出条件。"""
    for sig in generate_sell_signals(ctx):
        if sig.code != code:
            continue
        if signal_kind and sig.signal_kind != signal_kind:
            continue
        return True
    return False


def generate_sell_signals(ctx: ScoreContext) -> list[TradeSignal]:
    """产生卖出原始信号（未经三确认）。仅止损或趋势明确破位。"""
    mw_cfg = load_gates_config().get("main_wave") or {}
    sell_cfg = load_gates_config().get("sell") or {}
    signals: list[TradeSignal] = []

    for stock in get_holdings():
        code = str(stock.get("股票代码", "")).strip()
        if not code:
            continue
        enriched = stock
        for row in ctx.payload.get("持仓股") or []:
            if str(row.get("股票代码", "")).strip() == code:
                enriched = {**stock, **row}
                break

        price = _price(enriched)
        if price is None:
            continue
        try:
            buy_price = float(enriched.get("买入价", 0) or 0)
        except (TypeError, ValueError):
            buy_price = 0
        pnl_pct = (price - buy_price) / buy_price * 100 if buy_price > 0 else 0
        qty = int(enriched.get("持仓股数", 0) or 0)
        if qty < 100:
            continue

        buy_kind = _position_buy_kind(enriched)
        sell_type = ""
        kind = ""
        reason = ""

        ok_stop, stop_reason = stop_loss_triggers_sell(
            enriched,
            code,
            pnl_pct=pnl_pct,
            ctx=ctx,
            mw_cfg=mw_cfg,
            price=price,
        )
        if ok_stop:
            sell_type = "止损"
            kind = "止损"
            reason = stop_reason
        elif buy_kind == BUY_KIND_PULLBACK:
            m = mas_from_stock(enriched)
            ma20 = m.get("ma20")
            if ma20 and effective_ma20_break(price, ma20, mw_cfg):
                sell_type = "趋势衰竭"
                kind = SELL_KIND_TREND_ERODE
                reason = f"回调仓有效跌破MA20({ma20:.2f})"
        else:
            ok_trend, _phase, _trend_note = trend_allows_ascent_sell(enriched, mw_cfg)
            if ok_trend:
                ok, kind, reason = detect_sell_setup(enriched, ctx, mw_cfg)
                if ok:
                    sell_type = "破5日线" if kind == SELL_KIND_MA5_BREAK else "趋势衰竭"

        if not reason:
            continue

        signals.append(
            TradeSignal(
                action="卖出",
                code=code,
                name=str(enriched.get("股票名称", "")).strip(),
                price=price,
                quantity=qty,
                strategy=str(enriched.get("战法", STRATEGY_NAME)),
                reason=reason,
                sell_type=sell_type,
                signal_kind=kind,
            )
        )
    return signals
