"""扩展绩效指标。"""

from __future__ import annotations

import math
from typing import Any

from quant.backtest.broker import FillRecord, SimBroker


def _basic_metrics(broker: SimBroker) -> dict[str, Any]:
    from quant.backtest.metrics import compute_metrics

    return compute_metrics(broker)


def compute_extended_metrics(
    broker: SimBroker,
    *,
    trading_days: int = 0,
    benchmark_return: float | None = None,
) -> dict[str, Any]:
    """在基础指标上扩展 Sharpe/Sortino/Calmar/年化等。"""
    base = _basic_metrics(broker)
    curve = broker.equity_curve
    if len(curve) < 2:
        base.update(
            {
                "annualized_return": 0.0,
                "annualized_volatility": 0.0,
                "sharpe_ratio": 0.0,
                "sortino_ratio": 0.0,
                "calmar_ratio": 0.0,
                "avg_holding_days": 0.0,
                "turnover": 0.0,
            }
        )
        return base

    equities = [pt["equity"] for pt in curve]
    daily_rets: list[float] = []
    for i in range(1, len(equities)):
        if equities[i - 1] > 0:
            daily_rets.append((equities[i] - equities[i - 1]) / equities[i - 1])

    n_days = trading_days or max(len(daily_rets), 1)
    total_return = base.get("total_return", 0.0)
    ann_return = (1 + total_return) ** (252 / max(n_days, 1)) - 1 if n_days else 0.0

    if daily_rets:
        mean_r = sum(daily_rets) / len(daily_rets)
        var = sum((r - mean_r) ** 2 for r in daily_rets) / max(len(daily_rets) - 1, 1)
        vol = math.sqrt(var) * math.sqrt(252)
        downside = [min(0, r) for r in daily_rets]
        down_var = sum(d ** 2 for d in downside) / max(len(downside), 1)
        down_vol = math.sqrt(down_var) * math.sqrt(252)
    else:
        mean_r = vol = down_vol = 0.0

    sharpe = (ann_return / vol) if vol > 1e-9 else 0.0
    sortino = (ann_return / down_vol) if down_vol > 1e-9 else 0.0
    max_dd = float(base.get("max_drawdown", 0) or 0)
    calmar = (ann_return / max_dd) if max_dd > 1e-9 else 0.0

    fills = [t for t in broker.trades if not t.rejected and t.quantity > 0]
    buy_vol = sum(t.fill_price * t.quantity for t in fills if t.signal.action == "买入")
    avg_eq = sum(equities) / len(equities) if equities else broker.cfg.initial_cash
    turnover = buy_vol / avg_eq if avg_eq > 0 else 0.0

    base.update(
        {
            "annualized_return": round(ann_return, 4),
            "annualized_volatility": round(vol, 4),
            "sharpe_ratio": round(sharpe, 4),
            "sortino_ratio": round(sortino, 4),
            "calmar_ratio": round(calmar, 4),
            "turnover": round(turnover, 4),
            "excess_return_vs_benchmark": round(total_return - benchmark_return, 4)
            if benchmark_return is not None
            else None,
        }
    )
    return base
