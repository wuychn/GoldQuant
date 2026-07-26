"""回测绩效指标：年化收益 / 波动 / Sharpe / 最大回撤 / 换手 / 胜率 / 卡玛。"""

from __future__ import annotations

from typing import Any

import numpy as np

from quant.backtest2.broker import SimBroker


def _to_returns(equity: list[tuple[str, float]]) -> np.ndarray:
    if len(equity) < 2:
        return np.array([])
    vals = np.array([e[1] for e in equity], dtype=float)
    return vals[1:] / vals[:-1] - 1.0


def compute_metrics(broker: SimBroker, *, trading_days: int = 252) -> dict[str, Any]:
    eq = broker.equity_curve
    if len(eq) < 2:
        return {"total_return_pct": 0.0, "ann_return_pct": 0.0, "ann_vol_pct": 0.0, "sharpe": 0.0, "max_drawdown_pct": 0.0, "calmar": 0.0, "turnover_annual": 0.0, "win_rate": 0.0, "n_trades": 0, "n_days": len(eq)}

    rets = _to_returns(eq)
    vals = np.array([e[1] for e in eq], dtype=float)
    total_ret = vals[-1] / vals[0] - 1.0
    n = len(rets)
    ann_ret = (1 + total_ret) ** (trading_days / max(n, 1)) - 1.0
    ann_vol = float(rets.std(ddof=1) * np.sqrt(trading_days))
    sharpe = (ann_ret / ann_vol) if ann_vol > 1e-12 else 0.0

    # 最大回撤
    running_max = np.maximum.accumulate(vals)
    dd = (vals - running_max) / running_max
    max_dd = float(dd.min())

    calmar = ann_ret / abs(max_dd) if abs(max_dd) > 1e-12 else 0.0

    # 换手（双边成交额 / 平均权益，年化）
    avg_eq = float(vals.mean()) or 1.0
    total_turnover = sum(t.price * t.shares for t in broker.trades)
    turnover_annual = (total_turnover / avg_eq) * (trading_days / max(n, 1))

    # 胜率（按卖出 trade 的 pnl）
    sells = [t for t in broker.trades if t.side == "sell" and t.pnl is not None]
    win = sum(1 for t in sells if t.pnl > 0)
    win_rate = win / len(sells) if sells else 0.0

    return {
        "total_return_pct": round(total_ret * 100, 2),
        "ann_return_pct": round(ann_ret * 100, 2),
        "ann_vol_pct": round(ann_vol * 100, 2),
        "sharpe": round(sharpe, 3),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "calmar": round(calmar, 3),
        "turnover_annual": round(turnover_annual, 2),
        "win_rate": round(win_rate, 3),
        "n_trades": len(broker.trades),
        "n_days": len(eq),
        "final_equity": round(float(vals[-1]), 2),
    }
