"""原子交易执行器：信号 → 文件更新。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from quant.data_io import (
    append_stoploss_record,
    append_trade_log,
    append_trades,
    atomic_save_holdings_and_account_state,
    compute_holdings_market_value,
    get_cash,
    get_holdings,
    holding_codes_bought_on_calendar_date,
    merge_holdings_by_code,
    sync_profit_md_from_trades,
)
from quant.signals import TradeSignal
from quant.trading_hours import is_a_share_continuous_auction_window

_CASH_EPS = 1e-6


@dataclass
class ExecutedTrade:
    """已执行的交易记录。"""
    signal: TradeSignal
    timestamp: str      # HH:MM:SS
    pnl: float = 0.0   # 本次盈亏（仅卖出时有值）


def _format_trade_detail(trade: ExecutedTrade) -> str:
    """格式化交易记录详情（写入 trade_log）。"""
    s = trade.signal
    parts = [
        f"{s.name}({s.code})",
        f"价格{s.price:.2f}",
        f"{s.quantity // 100}手",
        f"[{s.strategy}]",
    ]
    if trade.pnl:
        parts.append(f"盈亏{trade.pnl:+.2f}元")
    if s.reason:
        parts.append(s.reason[:80])
    return " ".join(parts)


def _executed_to_record(trade: ExecutedTrade, *, date_str: str) -> dict:
    s = trade.signal
    return {
        "日期": date_str,
        "时间": trade.timestamp,
        "方向": s.action,
        "股票代码": s.code,
        "股票名称": s.name,
        "成交价": s.price,
        "股数": s.quantity,
        "战法": s.strategy,
        "理由": s.reason,
        "卖出类型": s.sell_type or "",
        "已实现盈亏": trade.pnl,
    }


def execute_signals(signals: list[TradeSignal]) -> list[ExecutedTrade]:
    """原子执行所有交易信号。

    卖出优先 → 买入；同代码多行持仓先合并；现金全程非负；成交后同步 profit.md。
    A 股 T+1：任一持仓行「买入时间」为当日则该代码不允许卖出（见 holding_codes_bought_on_calendar_date）。
    仅当 ``rules_config.yml`` 中 ``trading.enforce_real_workday`` 为 true 时：须在北京时间连续竞价时段
    （9:30～11:30、13:00～15:00）且为 ``_is_real_workday_single_day_api`` 判定之真实交易日。
    配置为 false 时不做时段与交易日限制。
    """
    if not signals:
        return []

    if not is_a_share_continuous_auction_window():
        print(
            "交易跳过：严格模式下须同时满足——北京时间连续竞价（9:30～11:30、13:00～15:00）"
            "且为真实交易日（见 _is_real_workday_single_day_api）。"
            "关闭限制请在 quant/rules_config.yml 中设 trading.enforce_real_workday: false"
        )
        return []

    cash = max(0.0, get_cash())
    today_d = datetime.now().date()
    raw_holdings = get_holdings()
    t1_locked_codes = holding_codes_bought_on_calendar_date(raw_holdings, today_d)
    holdings = merge_holdings_by_code(raw_holdings)
    timestamp = datetime.now().strftime("%H:%M:%S")
    date_str = datetime.now().strftime("%Y-%m-%d")
    executed: list[ExecutedTrade] = []

    holdings_idx: dict[str, int] = {}
    for i, h in enumerate(holdings):
        code = str(h.get("股票代码", "")).strip()
        if code:
            holdings_idx[code] = i

    to_remove: set[int] = set()

    for signal in signals:
        if signal.action != "卖出":
            continue

        idx = holdings_idx.get(signal.code)
        if idx is None:
            print(f"卖出跳过：{signal.name}({signal.code}) 不在持仓中")
            continue

        if signal.code in t1_locked_codes:
            print(
                f"卖出跳过：{signal.name}({signal.code}) "
                "A股T+1：当日买入不可当日卖出"
            )
            continue

        holding = holdings[idx]
        current_qty = 0
        raw_qty = holding.get("持仓股数", holding.get("数量", 0))
        try:
            current_qty = int(raw_qty)
        except (TypeError, ValueError):
            pass

        if current_qty <= 0:
            print(f"卖出跳过：{signal.name}({signal.code}) 持仓股数为0")
            continue

        actual_qty = min(signal.quantity, current_qty)
        if actual_qty <= 0:
            continue

        buy_price = 0.0
        try:
            buy_price = float(holding.get("买入价", 0))
        except (TypeError, ValueError):
            pass

        pnl = (signal.price - buy_price) * actual_qty
        cash = max(0.0, cash + signal.price * actual_qty)

        if actual_qty >= current_qty:
            to_remove.add(idx)
        else:
            holding["持仓股数"] = current_qty - actual_qty

        if signal.sell_type in ("止损", "时间止损"):
            today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            append_stoploss_record(
                signal.code, signal.name, today, signal.reason[:100]
            )

        executed.append(ExecutedTrade(signal, timestamp, pnl))

    holdings_after_sells = [h for i, h in enumerate(holdings) if i not in to_remove]

    for signal in signals:
        if signal.action != "买入":
            continue

        cost = signal.price * signal.quantity
        if cost > cash + _CASH_EPS:
            print(
                f"买入跳过：{signal.name}({signal.code}) 现金不足（需{cost:.2f}，"
                f"余{cash:.2f}）"
            )
            continue

        cash = max(0.0, cash - cost)
        holdings_after_sells.append({
            "股票代码": signal.code,
            "股票名称": signal.name,
            "买入价": signal.price,
            "买入时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "买入原因": signal.reason[:100],
            "战法": signal.strategy,
            "持仓股数": signal.quantity,
        })

        executed.append(ExecutedTrade(signal, timestamp))

    if not executed:
        return []

    final_holdings = merge_holdings_by_code(holdings_after_sells)
    position_mv = compute_holdings_market_value(final_holdings)
    total_pnl = sum(e.pnl for e in executed)
    atomic_save_holdings_and_account_state(
        final_holdings,
        cash,
        position_mv,
        last_batch_realized_pnl=total_pnl if total_pnl else None,
    )

    struct_rows = [_executed_to_record(e, date_str=date_str) for e in executed]
    append_trades(date_str, struct_rows)
    sync_profit_md_from_trades(date_str)

    for e in executed:
        append_trade_log(e.signal.action, _format_trade_detail(e))

    return executed
