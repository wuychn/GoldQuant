"""R2 买入：仅 combat 池 + 结构/intraday + ML gate。"""

from __future__ import annotations

from quant.config import load_gates_config
from quant.r2.config import load_r2_config
from quant.r2.domain.models import Regime, RegimeSnapshot
from quant.r2.io import state as state_io
from quant.r2.ml.runtime import score_entry
from quant.scoring.context import ScoreContext
from quant.scoring.tech_indicators import quote_last_price
from quant.trading.models import TradeSignal
from quant.strategy.intraday import intraday_allows_buy
from quant.strategy.main_wave import detect_buy_setup


def _position_qty(price: float, cash_hint: float = 500_000) -> int:
    if price <= 0:
        return 0
    budget = cash_hint * 0.1
    lots = int(budget / price / 100)
    return max(lots, 1) * 100


def generate_buy_signals(
    ctx: ScoreContext,
    *,
    regime: RegimeSnapshot,
    stock_by_code: dict[str, dict],
) -> list[TradeSignal]:
    if regime.regime == Regime.WEAK:
        return []
    gates = load_gates_config()
    mw = gates.get("main_wave") or {}
    buy_cfg = gates.get("buy") or {}
    r2 = load_r2_config()
    combat = {m.code: m for m in state_io.load_combat()}

    out: list[TradeSignal] = []
    for code, mem in combat.items():
        stock = stock_by_code.get(code)
        if not stock:
            continue
        ok, kind, reason = detect_buy_setup(stock, ctx, mw)
        if not ok:
            continue
        ok_intra, note = intraday_allows_buy(stock, buy_cfg, buy_kind=kind)
        if not ok_intra:
            continue

        mode = (r2.get("ml") or {}).get("mode", "shadow")
        min_prob = float((r2.get("ml") or {}).get("min_prob", 0.52))
        if mode == "gate":
            ml_prob = score_entry(stock, ctx.payload, regime=regime.regime.value)
            if ml_prob < min_prob:
                continue
        else:
            from quant.r2.signals.structure import structure_score

            ml_prob = max(0.0, min(1.0, structure_score(stock) / 100.0))

        price = quote_last_price(stock) or 0.0
        if price <= 0:
            continue
        out.append(
            TradeSignal(
                action="买入",
                code=code,
                name=mem.name or str(stock.get("股票名称", "")),
                price=price,
                quantity=_position_qty(price),
                strategy="R2主升浪",
                reason=f"{reason};{note};ml={ml_prob:.2f}",
                signal_kind=kind,
            )
        )
    return out
