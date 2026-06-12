"""回测绩效指标。"""

from __future__ import annotations

from typing import Any

from quant.backtest.broker import FillRecord, SimBroker


def compute_metrics(broker: SimBroker) -> dict[str, Any]:
    fills = [t for t in broker.trades if not t.rejected and t.quantity > 0]
    sells = [t for t in fills if t.signal.action == "卖出"]
    realized = sum(t.pnl for t in sells)
    wins = [t for t in sells if t.pnl > 0]
    losses = [t for t in sells if t.pnl <= 0]

    win_rate = len(wins) / len(sells) if sells else 0.0
    avg_win = sum(t.pnl for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(t.pnl for t in losses) / len(losses) if losses else 0.0
    profit_factor = (
        sum(t.pnl for t in wins) / abs(sum(t.pnl for t in losses))
        if losses and sum(t.pnl for t in losses) != 0
        else 0.0
    )

    curve = broker.equity_curve
    max_dd = 0.0
    peak = broker.cfg.initial_cash
    for pt in curve:
        eq = pt["equity"]
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)

    start_eq = broker.cfg.initial_cash
    end_eq = curve[-1]["equity"] if curve else start_eq
    total_return = (end_eq - start_eq) / start_eq if start_eq else 0.0

    rejected = sum(1 for t in broker.trades if t.rejected)

    return {
        "trade_count": len(fills),
        "sell_count": len(sells),
        "realized_pnl": round(realized, 2),
        "win_rate": round(win_rate, 4),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 4),
        "max_drawdown": round(max_dd, 4),
        "total_return": round(total_return, 4),
        "final_equity": round(end_eq, 2),
        "rejected_orders": rejected,
    }
