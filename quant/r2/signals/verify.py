"""R2 成交前复核（不依赖 R1 buy/sell 模块）。"""

from __future__ import annotations

from quant.config import load_gates_config
from quant.scoring.context import ScoreContext
from quant.strategy.main_wave import detect_buy_setup, detect_sell_setup


def _stock_from_ctx(ctx: ScoreContext, code: str) -> dict | None:
    for key in ("自选股", "持仓股"):
        for row in ctx.payload.get(key) or []:
            if isinstance(row, dict) and str(row.get("股票代码", "")).strip() == code:
                return row
    return None


def verify_buy_signal_still_valid(
    code: str,
    ctx: ScoreContext,
    *,
    mode: str = "during_market",
    signal_kind: str = "",
) -> bool:
    del mode, signal_kind
    stock = _stock_from_ctx(ctx, code)
    if not stock:
        return False
    mw = load_gates_config().get("main_wave") or {}
    ok, _, _ = detect_buy_setup(stock, ctx, mw)
    return ok


def verify_sell_signal_still_valid(
    code: str,
    ctx: ScoreContext,
    *,
    signal_kind: str = "",
) -> bool:
    del signal_kind
    stock = _stock_from_ctx(ctx, code)
    if not stock:
        return False
    mw = load_gates_config().get("main_wave") or {}
    ok, _, _ = detect_sell_setup(stock, ctx, mw)
    return ok
