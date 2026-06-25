"""卖出紧急度、止损判定与 14:30 执行窗口。"""

from __future__ import annotations

from quant.config import load_gates_config
from quant.constants import BUY_KIND_PULLBACK
from quant.execution.sim_rules import limit_pct, load_trade_sim_config
from quant.scoring.context import ScoreContext
from quant.scoring.tech_indicators import quote_change_pct
from quant.signals.models import TradeSignal
from quant.strategy.main_wave import detect_sell_setup
from quant.strategy.trend import effective_ma20_break, trend_allows_ascent_sell
from quant.trading_hours import is_late_session_for_trend_sell


def approaching_limit_down(stock: dict | None, code: str) -> bool:
    """现价趋近跌停（可早于 14:30 卖出）。"""
    if not stock:
        return False
    chg = quote_change_pct(stock)
    if chg is None:
        return False
    sell_cfg = load_gates_config().get("sell") or {}
    margin = float(sell_cfg.get("limit_down_approach_margin_pct", 1.0))
    lim = limit_pct(code, load_trade_sim_config())
    return chg <= -(lim - margin)


def near_limit_up(stock: dict | None, code: str) -> bool:
    """当日涨幅接近涨停（不应触发止损）。"""
    if not stock:
        return False
    chg = quote_change_pct(stock)
    if chg is None:
        return False
    sell_cfg = load_gates_config().get("sell") or {}
    margin = float(sell_cfg.get("limit_up_exempt_margin_pct", 0.5))
    lim = limit_pct(code, load_trade_sim_config())
    return chg >= lim - margin


def _position_buy_kind(holding: dict) -> str:
    kind = str(holding.get("买入类型") or "").strip()
    if kind:
        return kind
    if "回调企稳" in str(holding.get("买入原因") or ""):
        return BUY_KIND_PULLBACK
    return ""


def trend_broken_for_stop(
    stock: dict,
    price: float,
    *,
    ctx: ScoreContext,
    mw_cfg: dict,
) -> bool:
    """止损须伴随趋势转弱或关键均线破位（不破趋势不止损）。"""
    buy_kind = _position_buy_kind(stock)
    if buy_kind == BUY_KIND_PULLBACK:
        from quant.scoring.tech_indicators import mas_from_stock

        ma20 = mas_from_stock(stock).get("ma20")
        return bool(ma20 and effective_ma20_break(price, ma20, mw_cfg))

    ok_trend, _phase, _note = trend_allows_ascent_sell(stock, mw_cfg)
    if ok_trend:
        ok, _kind, _reason = detect_sell_setup(stock, ctx, mw_cfg)
        return ok
    return True


def stop_loss_exempt(stock: dict, code: str) -> bool:
    """强势日 / 近涨停：不因浮亏快照误止损。"""
    sell_cfg = load_gates_config().get("sell") or {}
    if bool(sell_cfg.get("stop_loss_exempt_near_limit_up", True)) and near_limit_up(stock, code):
        return True
    chg = quote_change_pct(stock)
    if chg is None:
        return False
    min_day = float(sell_cfg.get("stop_loss_exempt_min_day_chg_pct", 3.0))
    return chg >= min_day


def stop_loss_triggers_sell(
    stock: dict,
    code: str,
    *,
    pnl_pct: float,
    ctx: ScoreContext,
    mw_cfg: dict,
    price: float,
) -> tuple[bool, str]:
    """是否应发出止损类卖出。

    - 趋近跌停：允许（紧急，任意时刻）
    - 14:30 前：不因早盘浮亏快照止损（避免「早盘跌超 5% 午后拉涨停」误卖）
    - 仅浮亏达标但趋势完好 / 当日强阳：不允许
    - 浮亏达标且趋势已破位：14:30 后允许
    """
    sell_cfg = load_gates_config().get("sell") or {}
    threshold = float(sell_cfg.get("stop_loss_pct", -5.0))

    if approaching_limit_down(stock, code):
        return True, f"趋近跌停，浮盈亏{pnl_pct:.2f}%"

    if bool(sell_cfg.get("stop_loss_eval_after_late_session", True)):
        if not is_late_session_for_trend_sell():
            return False, ""

    if pnl_pct > threshold:
        return False, ""

    if stop_loss_exempt(stock, code):
        return False, ""

    require_break = bool(sell_cfg.get("stop_loss_require_trend_break", True))
    if require_break and not trend_broken_for_stop(stock, price, ctx=ctx, mw_cfg=mw_cfg):
        return False, ""

    return True, f"趋势转弱且浮亏{pnl_pct:.2f}%≤{threshold}%"


def is_urgent_sell(
    signal: TradeSignal,
    stock: dict | None,
    code: str,
) -> bool:
    """仅趋近跌停可早于 14:30 卖出（止损若触发也须等 14:30）。"""
    return approaching_limit_down(stock, code)


def sell_requires_late_session(
    signal: TradeSignal,
    stock: dict | None,
    code: str,
) -> bool:
    """非紧急卖出须 14:30 后成交。"""
    return not is_urgent_sell(signal, stock, code)
