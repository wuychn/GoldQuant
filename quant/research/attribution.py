"""PnL 归因。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from quant.backtest.broker import SimBroker


def attribute_pnl_by_signal_type(broker: SimBroker) -> dict[str, Any]:
    """按卖出类型 / 买入类型汇总已实现盈亏。"""
    by_type: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    buy_notional: dict[str, float] = defaultdict(float)
    for t in broker.trades:
        if t.rejected or t.quantity <= 0:
            continue
        sig = t.signal
        if sig.action == "卖出":
            key = sig.sell_type or "其他卖"
            by_type[key] += t.pnl
            counts[key] += 1
        elif sig.action == "买入":
            key = f"买-{sig.signal_kind or '未知'}"
            counts[key] += 1
            px = float(getattr(t, "fill_price", 0) or 0)
            buy_notional[key] += px * int(t.quantity)
    total_pnl = sum(by_type.values())
    return {
        "realized_by_sell_type": dict(by_type),
        "trade_counts": dict(counts),
        "buy_notional_by_kind": {k: round(v, 2) for k, v in buy_notional.items()},
        "total_realized_pnl": round(total_pnl, 2),
        "pnl_share_by_sell_type": {
            k: round(v / total_pnl, 4) if abs(total_pnl) > 1e-9 else 0.0
            for k, v in by_type.items()
        },
    }
