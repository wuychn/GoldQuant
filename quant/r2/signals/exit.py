"""R2 卖出：止损 / 止盈 / 逻辑证伪 / 趋势衰竭。"""

from __future__ import annotations

from quant.config import load_gates_config
from quant.r2.config import load_r2_config
from quant.r2.io import state as state_io
from quant.r2.io.state import load_sector_snapshot
from quant.scoring.context import ScoreContext
from quant.scoring.tech_indicators import quote_last_price
from quant.trading.models import TradeSignal
from quant.trading.sell_policy import near_limit_up
from quant.strategy.main_wave import detect_sell_setup


def _holding_qty(row: dict) -> int:
    for k in ("持仓股数", "持仓数量", "数量", "quantity"):
        try:
            v = int(float(row.get(k) or 0))
            if v > 0:
                return v
        except (TypeError, ValueError):
            continue
    return 0


def _pnl_pct(row: dict, price: float) -> float:
    try:
        buy = float(row.get("买入价") or 0)
    except (TypeError, ValueError):
        return 0.0
    if buy <= 0:
        return 0.0
    return (price - buy) / buy * 100


def _logic_falsified(sector_tags: list[str]) -> bool:
    faded = {
        r.get("name")
        for r in load_sector_snapshot()
        if isinstance(r, dict) and r.get("lifecycle") == "退潮"
    }
    return bool(faded & set(sector_tags or []))


def generate_sell_signals(
    ctx: ScoreContext,
    *,
    stock_by_code: dict[str, dict],
) -> list[TradeSignal]:
    cfg = load_gates_config()
    r2_exit = load_r2_config().get("exit") or {}
    combat = {m.code: m for m in state_io.load_combat()}

    trail_profit = float(r2_exit.get("trailing_profit_pct", 5.0))
    trail_drop = float(r2_exit.get("trailing_drop_pct", 4.0))
    stop_loss_pct = float(r2_exit.get("stop_loss_pct", 7.0))
    mw_cfg = cfg.get("main_wave") or {}

    out: list[TradeSignal] = []
    for row in ctx.payload.get("持仓股") or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("股票代码", "")).strip()
        if not code:
            continue
        stock = stock_by_code.get(code, row)
        qty = _holding_qty(row)
        if qty <= 0:
            continue
        price = quote_last_price(stock) or 0.0
        name = str(stock.get("股票名称") or row.get("股票名称") or "")
        pnl = _pnl_pct(row, price)

        if pnl <= -stop_loss_pct and not near_limit_up(stock, code):
            out.append(
                TradeSignal(
                    action="卖出",
                    code=code,
                    name=name,
                    price=price,
                    quantity=qty,
                    strategy="R2主升浪",
                    reason="止损",
                    sell_type="止损",
                )
            )
            continue

        pk = stock.get("盘口") if isinstance(stock.get("盘口"), dict) else {}
        high = pk.get("最高")
        try:
            high_f = float(high) if high is not None else 0.0
        except (TypeError, ValueError):
            high_f = 0.0
        if pnl >= trail_profit and high_f > 0 and price > 0:
            dd = (price - high_f) / high_f * 100
            if dd <= -trail_drop:
                out.append(
                    TradeSignal(
                        action="卖出",
                        code=code,
                        name=name,
                        price=price,
                        quantity=qty,
                        strategy="R2主升浪",
                        reason="移动止盈",
                        sell_type="止盈",
                    )
                )
                continue

        mem = combat.get(code)
        if mem and _logic_falsified(mem.sector_tags):
            out.append(
                TradeSignal(
                    action="卖出",
                    code=code,
                    name=name,
                    price=price,
                    quantity=qty,
                    strategy="R2主升浪",
                    reason="板块退潮逻辑证伪",
                    sell_type="逻辑证伪",
                )
            )
            continue

        ok, sell_kind, reason = detect_sell_setup(stock, ctx, mw_cfg)
        if ok:
            out.append(
                TradeSignal(
                    action="卖出",
                    code=code,
                    name=name,
                    price=price,
                    quantity=qty,
                    strategy="R2主升浪",
                    reason=reason,
                    sell_type=sell_kind or "趋势衰竭",
                )
            )
    return out
