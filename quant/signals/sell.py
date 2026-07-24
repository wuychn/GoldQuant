"""卖出原始信号：止损（紧急）+ 止盈 + 日亏强减 + 趋势破位（14:30 后）。"""

from __future__ import annotations

from quant.config import load_gates_config, load_scoring_config
from quant.constants import (
    BUY_KIND_PULLBACK,
    SELL_KIND_MA5_BREAK,
    SELL_KIND_SCORE_WEAK,
    SELL_KIND_TREND_ERODE,
    STRATEGY_NAME,
)
from quant.portfolio.risk import daily_loss_force_reduce
from quant.scoring.context import ScoreContext
from quant.scoring.engine import ScoringEngine
from quant.signals.models import TradeSignal
from quant.store.state import get_holdings, get_total_assets, sum_today_realized_pnl
from quant.scoring.tech_indicators import mas_from_stock, quote_last_price
from quant.signals.sell_policy import (
    score_weakness_triggers_sell,
    stop_loss_triggers_sell,
    take_profit_triggers_sell,
)
from quant.strategy.main_wave import detect_sell_setup
from quant.strategy.time_stop import parse_buy_date, time_stop_triggers_sell
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
    """产生卖出原始信号（未经三确认）。"""
    mw_cfg = load_gates_config().get("main_wave") or {}
    sell_cfg = load_gates_config().get("sell") or {}
    sell_threshold = float(load_scoring_config().get("sell_threshold", 45))
    score_engine = ScoringEngine()
    signals: list[TradeSignal] = []

    total = get_total_assets()
    daily_pnl = sum_today_realized_pnl()
    daily_pnl_pct = (daily_pnl / total * 100) if total > 0 else 0.0
    force_reduce = daily_loss_force_reduce(daily_pnl_pct)

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

        if force_reduce:
            sell_type = "日亏强制减仓"
            kind = "日亏强制减仓"
            reason = f"当日已实现亏损{daily_pnl_pct:.2f}%触发强制减仓"
        else:
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
            else:
                ok_tp, tp_reason = take_profit_triggers_sell(enriched, pnl_pct=pnl_pct)
                if ok_tp:
                    sell_type = "止盈"
                    kind = "止盈"
                    reason = tp_reason
                else:
                    ok_ts, ts_reason = time_stop_triggers_sell(
                        enriched,
                        sell_cfg,
                        pnl_pct=pnl_pct,
                        buy_date=parse_buy_date(enriched),
                    )
                    if ok_ts:
                        sell_type = "时间止损"
                        kind = "时间止损"
                        reason = ts_reason
                    else:
                        holding_score = score_engine.score_stock(ctx, enriched)
                        ok_sw, sw_reason = score_weakness_triggers_sell(
                            score_total=holding_score.total,
                            sell_threshold=sell_threshold,
                        )
                        if ok_sw:
                            sell_type = SELL_KIND_SCORE_WEAK
                            kind = SELL_KIND_SCORE_WEAK
                            reason = sw_reason

        if not reason and buy_kind == BUY_KIND_PULLBACK:
            m = mas_from_stock(enriched)
            ma20 = m.get("ma20")
            if ma20 and effective_ma20_break(price, ma20, mw_cfg):
                sell_type = "趋势衰竭"
                kind = SELL_KIND_TREND_ERODE
                reason = f"回调仓有效跌破MA20({ma20:.2f})"
        elif not reason:
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
