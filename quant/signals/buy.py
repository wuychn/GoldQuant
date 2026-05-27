"""买入原始信号：仅主升浪战法，仅自选股。"""

from __future__ import annotations

from quant.config import load_gates_config
from quant.constants import STRATEGY_NAME
from quant.gates.rules import calc_buy_quantity, check_buy_gates
from quant.scoring.context import ScoreContext
from quant.scoring.engine import ScoringEngine
from quant.signals.models import TradeSignal
from quant.store.state import get_holdings
from quant.strategy.main_wave import detect_buy_setup


def _price(stock: dict) -> float | None:
    pk = stock.get("盘口") if isinstance(stock.get("盘口"), dict) else {}
    for key in ("最新", "今开", "最新价"):
        v = pk.get(key)
        if v is not None:
            try:
                p = float(v)
                if p > 0:
                    return p
            except (TypeError, ValueError):
                continue
    return None


def generate_buy_signals(ctx: ScoreContext, *, mode: str) -> list[TradeSignal]:
    """产生买入原始信号（未经三确认，勿直接 execute）。"""
    mw_cfg = load_gates_config().get("main_wave") or {}
    buy_cfg = (load_gates_config().get("buy") or {}).get("during_market" if mode == "during_market" else "pre_market") or {}
    engine = ScoringEngine()
    held = {str(h.get("股票代码", "")).strip() for h in get_holdings()}
    signals: list[TradeSignal] = []

    for stock in ctx.payload.get("自选股") or []:
        code = str(stock.get("股票代码", "")).strip()
        if not code or code in held:
            continue
        if not check_buy_gates(stock, ctx).passed:
            continue

        score = engine.score_stock(ctx, stock)
        if score.total < float(engine.config.get("buy_threshold", 72)):
            continue

        ok, kind, reason = detect_buy_setup(stock, ctx, mw_cfg)
        if not ok:
            continue

        price = _price(stock)
        if price is None:
            continue

        if mode == "during_market":
            pk = stock.get("盘口") if isinstance(stock.get("盘口"), dict) else {}
            try:
                chg = float(pk.get("涨幅", 0) or 0)
            except (TypeError, ValueError):
                chg = 0
            if chg >= float(buy_cfg.get("max_change_pct", 8.0)):
                continue

        qty = calc_buy_quantity({**stock, "战法": STRATEGY_NAME}, ctx, price)
        if qty < 100:
            continue

        signals.append(
            TradeSignal(
                action="买入",
                code=code,
                name=str(stock.get("股票名称", "")).strip(),
                price=price,
                quantity=qty,
                strategy=STRATEGY_NAME,
                reason=f"[{kind}]评分{score.total:.1f}；{reason}",
                signal_kind=kind,
            )
        )
    return signals
