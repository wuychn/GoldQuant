"""卖出紧急度与 14:30 执行窗口。

r3 卖出由 ``exit/rules`` 驱动（晚间 sell_watch 算 stop 价 → 盘中价触发 → 撮合），
本模块仅保留"非紧急卖出须 14:30 后"的时段门：趋近跌停可早卖，其余等尾盘。
不再含止损/止盈/评分去弱逻辑（那些由 exit/rules 的 ATR/硬止损/破位/时间止损覆盖）。
"""

from __future__ import annotations

from quant.config import load_gates_config
from quant.data.quote import quote_change_pct
from quant.execution.sim_rules import limit_pct, load_trade_sim_config
from quant.signals.models import TradeSignal


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


def is_urgent_sell(
    signal: TradeSignal,
    stock: dict | None,
    code: str,
) -> bool:
    """仅趋近跌停可早于 14:30 卖出。"""
    return approaching_limit_down(stock, code)


def sell_requires_late_session(
    signal: TradeSignal,
    stock: dict | None,
    code: str,
) -> bool:
    """非紧急卖出须 14:30 后成交。"""
    return not is_urgent_sell(signal, stock, code)
