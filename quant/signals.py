"""信号生成层：从规则链结果转换为可执行的交易信号。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from quant.data_io import holding_codes_bought_on_calendar_date
from quant.trading_hours import is_a_share_continuous_auction_window
from quant.rules.base import ChainResult, RuleChain, RuleResult, RuleVerdict
from quant.rules.context import RuleContext


@dataclass
class TradeSignal:
    """一条可执行的交易信号。"""
    action: str         # "买入" | "卖出"
    code: str           # 股票代码
    name: str           # 股票名称
    price: float        # 执行价格（盘口最新价）
    quantity: int       # 股数（100 的整数倍）
    strategy: str       # "涨停板战法" | "龙回头战法" | "主升浪战法"
    reason: str         # 规则引擎给出的综合理由
    sell_type: str = "" # 卖出类型："清仓"|"减半"|"止损"|"止盈"|"时间止损"


# ---------------------------------------------------------------------------
# 辅助：从股票 dict 中提取盘口最新价
# ---------------------------------------------------------------------------

def _get_latest_price(stock: dict[str, Any]) -> float | None:
    pankou = stock.get("盘口", {})
    v = pankou.get("最新")
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _get_strategy(stock: dict[str, Any]) -> str:
    tag = str(stock.get("战法", "") or "").strip()
    if "涨停" in tag:
        return "涨停板战法"
    if "主升浪" in tag:
        return "主升浪战法"
    if "龙回头" in tag:
        return "龙回头战法"
    reason = str(stock.get("加入自选原因", "") or stock.get("买入原因", "") or "").strip()
    if reason.startswith("【涨停板战法】"):
        return "涨停板战法"
    if reason.startswith("【主升浪战法】"):
        return "主升浪战法"
    if reason.startswith("【龙回头战法】"):
        return "龙回头战法"
    return tag or "未知"


def _round_down_to_lot(shares: int) -> int:
    """向下取整到 100 股整数倍。"""
    return (shares // 100) * 100


# ---------------------------------------------------------------------------
# 买入信号生成
# ---------------------------------------------------------------------------

def generate_buy_signals(ctx: RuleContext, chains: dict[str, RuleChain]) -> list[TradeSignal]:
    """遍历自选股，运行买入链，为 all_passed 的标的生成买入信号。"""
    if not is_a_share_continuous_auction_window():
        return []
    signals: list[TradeSignal] = []

    for stock in ctx.watchlist:
        code = str(stock.get("股票代码", "")).strip()
        name = str(stock.get("股票名称", "")).strip()
        if not code:
            continue

        strategy = _get_strategy(stock)

        # 选择对应买入链
        if strategy == "涨停板战法":
            chain = chains.get("zt_buy_intraday") or chains.get("zt_buy_pre")
        elif strategy == "主升浪战法":
            chain = chains.get("zsll_buy")
        elif strategy == "龙回头战法":
            chain = chains.get("lht_buy")
        else:
            continue

        if chain is None:
            continue

        # 设置目标股票并运行链
        ctx.target_stock = stock
        result = chain.evaluate(ctx)

        if not result.all_passed:
            continue

        # 获取价格
        price = _get_latest_price(stock)
        if price is None or price <= 0:
            continue

        # 从 ctx.extra 读取仓位比例（由 PositionLimitRule 写入）
        max_single = ctx.extra.get("max_single_position", 0.10)
        amount = ctx.fund * max_single
        quantity = _round_down_to_lot(int(amount / price))

        if quantity <= 0:
            continue

        # 汇总通过的规则理由
        reasons = "; ".join(r.reason for r in result.passes if r.reason)

        signals.append(TradeSignal(
            action="买入",
            code=code,
            name=name,
            price=price,
            quantity=quantity,
            strategy=strategy,
            reason=reasons,
        ))

    return signals


# ---------------------------------------------------------------------------
# 卖出信号生成
# ---------------------------------------------------------------------------

# 卖出类型 → 是否清仓
_CLEAR_ALL_TYPES = frozenset({"清仓", "止损", "时间止损"})


def _determine_sell_from_chain(result: ChainResult, stock: dict[str, Any]) -> tuple[str, str] | None:
    """从 hold + sell 链结果中提取第一个卖出信号。

    Returns:
        (sell_type, reason) 或 None（无卖出信号）
    """
    for r in result.results:
        if r.verdict != RuleVerdict.FAIL:
            continue
        sell_type = r.data.get("sell_type", "")
        if sell_type:
            return sell_type, r.reason
    return None


def generate_sell_signals(ctx: RuleContext, chains: dict[str, RuleChain]) -> list[TradeSignal]:
    """遍历持仓股，运行持股监控+卖出链，为触发条件的标的生成卖出信号。"""
    if not is_a_share_continuous_auction_window():
        return []
    signals: list[TradeSignal] = []
    t1_locked = holding_codes_bought_on_calendar_date(
        ctx.holdings, datetime.now().date()
    )

    for stock in ctx.holdings:
        code = str(stock.get("股票代码", "")).strip()
        name = str(stock.get("股票名称", "")).strip()
        if not code:
            continue

        if code in t1_locked:
            continue

        strategy = _get_strategy(stock)

        # 选择对应的 hold + sell 链
        if strategy == "涨停板战法":
            hold_chain = chains.get("zt_hold")
            sell_chain = chains.get("zt_sell")
        elif strategy == "主升浪战法":
            hold_chain = chains.get("zsll_hold")
            sell_chain = chains.get("zsll_sell")
        elif strategy == "龙回头战法":
            hold_chain = chains.get("lht_hold")
            sell_chain = chains.get("lht_sell")
        else:
            continue

        # 设置目标股票
        ctx.target_stock = stock

        # 运行 hold + sell 链，合并结果
        sell_info = None

        # hold 链中 FAIL = 持股异常（减仓信号）
        if hold_chain:
            hold_result = hold_chain.evaluate(ctx)
            sell_info = _determine_sell_from_chain(hold_result, stock)

        # sell 链中 FAIL = 卖出条件触发
        if sell_info is None and sell_chain:
            sell_result = sell_chain.evaluate(ctx)
            sell_info = _determine_sell_from_chain(sell_result, stock)
        elif sell_chain:
            # 即使 hold 链已触发，也检查 sell 链是否有更强的信号（如止损优先于减半）
            sell_result = sell_chain.evaluate(ctx)
            alt_info = _determine_sell_from_chain(sell_result, stock)
            if alt_info and alt_info[0] in _CLEAR_ALL_TYPES:
                sell_info = alt_info  # 止损/时间止损/清仓 覆盖 减半

        if sell_info is None:
            continue

        sell_type, reason = sell_info

        # 获取价格
        price = _get_latest_price(stock)
        if price is None or price <= 0:
            continue

        # 计算卖出数量
        current_qty = 0
        raw_qty = stock.get("持仓股数", stock.get("数量", 0))
        try:
            current_qty = int(raw_qty)
        except (TypeError, ValueError):
            pass

        if current_qty <= 0:
            continue

        if sell_type in _CLEAR_ALL_TYPES:
            quantity = current_qty
        else:
            # 减半
            quantity = _round_down_to_lot(current_qty // 2)
            if quantity <= 0:
                quantity = current_qty  # 不足 200 股时全部卖出

        signals.append(TradeSignal(
            action="卖出",
            code=code,
            name=name,
            price=price,
            quantity=quantity,
            strategy=strategy,
            reason=reason,
            sell_type=sell_type,
        ))

    return signals
