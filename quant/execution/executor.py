"""模拟成交：信号 → 更新 state/ 与 daily/trades/。

规则
----
- 先卖后买（同批信号内）
- A 股 T+1：当日买入的代码不可当日卖出
- 100 股整数倍；可用资金不足则跳过买入
- 佣金万一（最低 5 元）、卖出印花税、沪市过户费、滑点、涨跌停不可成交
- 受 trading_hours 连续竞价时段约束（quant.yml gates.trading 可关闭）
"""

from __future__ import annotations

from dataclasses import dataclass

from quant.execution.sim_rules import (
    at_limit_up_down,
    calc_buy_cost,
    calc_sell_proceeds,
    load_trade_sim_config,
)
from quant.signals.models import TradeSignal
from quant.store.state import (
    append_stoploss,
    append_trade,
    codes_sold_today,
    compute_holdings_market_value,
    get_cash,
    get_holdings,
    holding_codes_bought_today,
    merge_holdings_by_code,
    save_account,
    save_holdings,
)
from quant.timeutil import cn_date_str, cn_datetime_str, cn_time_str
from quant.signals.sell_policy import sell_requires_late_session
from quant.trading_hours import is_a_share_continuous_auction_window, is_late_session_for_trend_sell

_CASH_EPS = 1e-6


@dataclass
class ExecutedTrade:
    signal: TradeSignal
    timestamp: str
    pnl: float = 0.0
    fill_price: float = 0.0
    commission: float = 0.0
    stamp_tax: float = 0.0
    transfer_fee: float = 0.0


def _sell_requires_late_session(
    signal: TradeSignal,
    stock: dict | None,
    code: str,
) -> bool:
    """非紧急卖出须 14:30 后成交。"""
    return sell_requires_late_session(signal, stock, code)


def _stock_map(payload: dict | None) -> dict[str, dict]:
    m: dict[str, dict] = {}
    if not payload:
        return m
    for key in ("自选股", "持仓股"):
        for row in payload.get(key) or []:
            if isinstance(row, dict):
                code = str(row.get("股票代码", "")).strip()
                if code:
                    m[code] = row
    return m


def execute_signals(
    signals: list[TradeSignal],
    *,
    payload: dict | None = None,
) -> list[ExecutedTrade]:
    if not signals:
        return []
    if not is_a_share_continuous_auction_window():
        print("交易跳过：不在连续竞价时段或未启用时间豁免")
        return []

    sim = load_trade_sim_config()
    quotes = _stock_map(payload)
    cash = max(0.0, get_cash())
    holdings = merge_holdings_by_code(get_holdings())
    t1_locked = holding_codes_bought_today(holdings)
    sold_today = codes_sold_today()
    ts = cn_time_str()
    date_str = cn_date_str()
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
        stock = quotes.get(signal.code) or holdings[i]
        if _sell_requires_late_session(signal, stock, signal.code) and not is_late_session_for_trend_sell():
            print(f"卖出跳过（等待14:30后执行）：{signal.name}({signal.code}) {signal.sell_type}")
            continue
        if at_limit_up_down(stock, signal.code, side="sell", cfg=sim):
            print(f"卖出跳过 跌停：{signal.name}({signal.code})")
            continue
        h = holdings[i]
        qty = int(h.get("持仓股数", 0) or 0)
        if qty <= 0:
            continue
        actual = min(signal.quantity, qty)
        buy_price = float(h.get("买入价", 0) or 0)
        proceeds = calc_sell_proceeds(signal.price, actual, signal.code, buy_price, sim)
        cash += proceeds.net_proceeds
        if actual >= qty:
            to_remove.add(i)
        else:
            h["持仓股数"] = qty - actual
        if signal.sell_type in ("止损", "时间止损"):
            append_stoploss(signal.code, signal.name, signal.reason)
        executed.append(
            ExecutedTrade(
                signal,
                ts,
                pnl=round(proceeds.pnl, 2),
                fill_price=proceeds.fill_price,
                commission=proceeds.commission,
                stamp_tax=proceeds.stamp_tax,
                transfer_fee=proceeds.transfer_fee,
            )
        )
        append_trade(
            date_str,
            _trade_record(signal, ts, date_str, actual, proceeds),
        )

    holdings = [h for i, h in enumerate(holdings) if i not in to_remove]

    # --- 第二阶段：买入 ---
    held_codes = {str(h.get("股票代码", "")).strip() for h in holdings}
    for signal in signals:
        if signal.action != "买入":
            continue
        if signal.code in sold_today:
            print(f"买入跳过 当日已卖：{signal.name}({signal.code})")
            continue
        if signal.code in held_codes:
            continue
        stock = quotes.get(signal.code) or {}
        if at_limit_up_down(stock, signal.code, side="buy", cfg=sim):
            print(f"买入跳过 涨停：{signal.name}({signal.code})")
            continue
        cost = calc_buy_cost(signal.price, signal.quantity, signal.code, sim)
        if cost.total > cash + _CASH_EPS:
            print(
                f"买入跳过 可用不足：{signal.name}({signal.code}) "
                f"需{cost.total:.2f}含佣{cost.commission:.2f}"
            )
            continue
        cash -= cost.total
        holdings.append(
            {
                "股票代码": signal.code,
                "股票名称": signal.name,
                "买入价": cost.fill_price,
                "买入时间": cn_datetime_str(),
                "买入类型": signal.signal_kind or "",
                "买入原因": signal.reason[:120],
                "战法": signal.strategy,
                "持仓股数": signal.quantity,
            }
        )
        held_codes.add(signal.code)
        executed.append(
            ExecutedTrade(
                signal,
                ts,
                fill_price=cost.fill_price,
                commission=cost.commission,
                transfer_fee=cost.transfer_fee,
            )
        )
        append_trade(date_str, _trade_record(signal, ts, date_str, signal.quantity, cost))

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
    breakdown: object,
) -> dict:
    from quant.execution.sim_rules import BuyCostBreakdown, SellProceedsBreakdown

    rec: dict = {
        "日期": date_str,
        "时间": ts,
        "方向": signal.action,
        "股票代码": signal.code,
        "股票名称": signal.name,
        "股数": qty,
        "战法": signal.strategy,
        "理由": signal.reason,
        "卖出类型": signal.sell_type or "",
    }
    if isinstance(breakdown, SellProceedsBreakdown):
        rec.update(
            {
                "成交价": breakdown.fill_price,
                "成交额": round(breakdown.amount, 2),
                "佣金": breakdown.commission,
                "印花税": breakdown.stamp_tax,
                "过户费": breakdown.transfer_fee,
                "已实现盈亏": round(breakdown.pnl, 2),
            }
        )
    elif isinstance(breakdown, BuyCostBreakdown):
        rec.update(
            {
                "成交价": breakdown.fill_price,
                "成交额": round(breakdown.amount, 2),
                "佣金": breakdown.commission,
                "过户费": breakdown.transfer_fee,
                "已实现盈亏": 0,
            }
        )
    else:
        rec["成交价"] = signal.price
        rec["已实现盈亏"] = 0
    return rec
