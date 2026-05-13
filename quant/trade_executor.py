"""原子交易执行器：信号 → 文件更新。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from quant.data_io import (
    append_stoploss_record,
    append_trade_log,
    get_fund,
    get_holdings,
    save_holdings,
    update_fund,
)
from quant.signals import TradeSignal


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


def execute_signals(signals: list[TradeSignal]) -> list[ExecutedTrade]:
    """原子执行所有交易信号。

    执行顺序：卖出优先（释放资金）→ 买入。
    所有计算在内存中完成后一次性写入文件。
    """
    if not signals:
        return []

    # 1. 获取当前状态快照
    holdings = get_holdings()
    fund = get_fund()
    timestamp = datetime.now().strftime("%H:%M:%S")
    executed: list[ExecutedTrade] = []

    # 建立持仓索引：code -> list index
    holdings_idx: dict[str, int] = {}
    for i, h in enumerate(holdings):
        code = str(h.get("股票代码", "")).strip()
        if code:
            holdings_idx[code] = i

    # 标记需要删除的持仓
    to_remove: set[int] = set()

    # 2. 卖出优先
    for signal in signals:
        if signal.action != "卖出":
            continue

        idx = holdings_idx.get(signal.code)
        if idx is None:
            print(f"卖出跳过：{signal.name}({signal.code}) 不在持仓中")
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

        # 计算盈亏
        buy_price = 0.0
        try:
            buy_price = float(holding.get("买入价", 0))
        except (TypeError, ValueError):
            pass

        pnl = (signal.price - buy_price) * actual_qty

        # 回收资金
        fund += signal.price * actual_qty

        # 更新或删除持仓记录
        if actual_qty >= current_qty:
            # 清仓：标记删除
            to_remove.add(idx)
        else:
            # 减仓：更新股数
            holding["持仓股数"] = current_qty - actual_qty

        # 止损记录
        if signal.sell_type in ("止损", "时间止损"):
            today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            append_stoploss_record(
                signal.code, signal.name, today, signal.reason[:100]
            )

        executed.append(ExecutedTrade(signal, timestamp, pnl))

    # 3. 买入
    for signal in signals:
        if signal.action != "买入":
            continue

        cost = signal.price * signal.quantity
        if cost > fund:
            print(f"买入跳过：{signal.name}({signal.code}) 资金不足（需{cost:.0f}，余{fund:.0f}）")
            continue

        fund -= cost
        holdings.append({
            "股票代码": signal.code,
            "股票名称": signal.name,
            "买入价": signal.price,
            "买入时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "买入原因": signal.reason[:100],
            "战法": signal.strategy,
            "持仓股数": signal.quantity,
        })

        executed.append(ExecutedTrade(signal, timestamp))

    # 4. 原子写入
    if not executed:
        return []

    # 移除已清仓的持仓
    final_holdings = [h for i, h in enumerate(holdings) if i not in to_remove]
    save_holdings(final_holdings)

    # 更新资金
    total_pnl = sum(e.pnl for e in executed)
    if total_pnl != 0:
        update_fund(total_pnl)

    # 写操作记录
    for e in executed:
        append_trade_log(e.signal.action, _format_trade_detail(e))

    return executed
