"""模拟成交：信号 → 更新 state/ 与 daily/trades/。

规则
----
- 先卖后买（同批信号内）
- A 股 T+1：当日买入的代码不可当日卖出
- 100 股整数倍；可用资金不足则跳过买入
- 受 trading_hours 连续竞价时段约束（quant.yml gates.trading 可关闭）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from quant.signals.models import TradeSignal
from quant.store.state import (
    append_stoploss,
    append_trade,
    compute_holdings_market_value,
    get_cash,
    get_holdings,
    holding_codes_bought_today,
    merge_holdings_by_code,
    save_account,
    save_holdings,
)
from quant.trading_hours import is_a_share_continuous_auction_window, is_late_session_for_trend_sell

_CASH_EPS = 1e-6


@dataclass
class ExecutedTrade:
    signal: TradeSignal
    timestamp: str
    pnl: float = 0.0


def execute_signals(signals: list[TradeSignal]) -> list[ExecutedTrade]:
    if not signals:
        return []
    if not is_a_share_continuous_auction_window():
        print("交易跳过：不在连续竞价时段或未启用时间豁免")
        return []

    cash = max(0.0, get_cash())
    holdings = merge_holdings_by_code(get_holdings())
    t1_locked = holding_codes_bought_today(holdings)
    ts = datetime.now().strftime("%H:%M:%S")
    date_str = datetime.now().strftime("%Y-%m-%d")
    executed: list[ExecutedTrade] = []
    idx_map = {str(h.get("股票代码", "")).strip(): i for i, h in enumerate(holdings)}
    to_remove: set[int] = set()

    # --- 第一阶段：卖出 ---
    for signal in signals:
        if signal.action != "卖出":
            continue
        i = idx_map.get(signal.code)
        if i is None:
            continue
        if signal.code in t1_locked:
            print(f"卖出跳过 T+1：{signal.name}({signal.code})")
            continue
        if signal.sell_type not in ("止损", "时间止损") and not is_late_session_for_trend_sell():
            print(f"卖出跳过（等待神奇2点30最终确认）：{signal.name}({signal.code}) {signal.sell_type}")
            continue
        h = holdings[i]
        qty = int(h.get("持仓股数", 0) or 0)
        if qty <= 0:
            continue
        actual = min(signal.quantity, qty)
        buy_price = float(h.get("买入价", 0) or 0)
        pnl = (signal.price - buy_price) * actual
        cash += signal.price * actual
        if actual >= qty:
            to_remove.add(i)
        else:
            h["持仓股数"] = qty - actual
        if signal.sell_type in ("止损", "时间止损"):
            append_stoploss(signal.code, signal.name, signal.reason)
        executed.append(ExecutedTrade(signal, ts, pnl))
        append_trade(date_str, _trade_record(signal, ts, date_str, actual, pnl))

    holdings = [h for i, h in enumerate(holdings) if i not in to_remove]

    # --- 第二阶段：买入 ---
    for signal in signals:
        if signal.action != "买入":
            continue
        cost = signal.price * signal.quantity
        if cost > cash + _CASH_EPS:
            print(f"买入跳过 可用不足：{signal.name}({signal.code})")
            continue
        cash -= cost
        holdings.append(
            {
                "股票代码": signal.code,
                "股票名称": signal.name,
                "买入价": signal.price,
                "买入时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "买入原因": signal.reason[:120],
                "战法": signal.strategy,
                "持仓股数": signal.quantity,
            }
        )
        executed.append(ExecutedTrade(signal, ts))
        append_trade(date_str, _trade_record(signal, ts, date_str, signal.quantity, 0))

    if not executed:
        return []

    final = merge_holdings_by_code(holdings)
    mv = compute_holdings_market_value(final)
    realized = sum(e.pnl for e in executed)
    save_account(cash=cash, position_mv=mv, daily_realized_delta=realized)
    save_holdings(final)
    return executed


def _trade_record(
    signal: TradeSignal,
    ts: str,
    date_str: str,
    qty: int,
    pnl: float,
) -> dict:
    return {
        "日期": date_str,
        "时间": ts,
        "方向": signal.action,
        "股票代码": signal.code,
        "股票名称": signal.name,
        "成交价": signal.price,
        "股数": qty,
        "战法": signal.strategy,
        "理由": signal.reason,
        "卖出类型": signal.sell_type or "",
        "已实现盈亏": pnl,
    }
