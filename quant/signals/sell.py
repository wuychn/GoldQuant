"""卖出原始信号：主升浪趋势变化卖点。"""

from __future__ import annotations

from quant.config import load_gates_config
from quant.constants import SELL_KIND_MA5_BREAK, SELL_KIND_TREND_ERODE, STRATEGY_NAME
from quant.scoring.context import ScoreContext
from quant.scoring.engine import ScoringEngine
from quant.signals.models import TradeSignal
from quant.store.state import get_holdings
from quant.strategy.main_wave import detect_sell_setup


def _price(stock: dict) -> float | None:
    pk = stock.get("盘口") if isinstance(stock.get("盘口"), dict) else {}
    for key in ("最新", "最新价"):
        v = pk.get(key)
        if v is not None:
            try:
                p = float(v)
                if p > 0:
                    return p
            except (TypeError, ValueError):
                continue
    return None


def generate_sell_signals(ctx: ScoreContext) -> list[TradeSignal]:
    """产生卖出原始信号（未经三确认）。"""
    mw_cfg = load_gates_config().get("main_wave") or {}
    sell_cfg = load_gates_config().get("sell") or {}
    engine = ScoringEngine()
    stop_loss = float(sell_cfg.get("stop_loss_pct", -5.0))
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

        score = engine.score_stock(ctx, enriched)
        sell_type = ""
        kind = ""
        reason = ""

        if pnl_pct <= stop_loss:
            sell_type = "止损"
            kind = "止损"
            reason = f"浮亏{pnl_pct:.2f}%≤{stop_loss}%"
        else:
            ok, kind, reason = detect_sell_setup(enriched, ctx, mw_cfg)
            if ok:
                sell_type = "破5日线" if kind == SELL_KIND_MA5_BREAK else "趋势衰竭"
            elif score.total < float(engine.config.get("sell_threshold", 45)):
                sell_type = "去弱留强"
                kind = "评分走弱"
                reason = f"持仓评分{score.total:.1f}低于阈值"

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
