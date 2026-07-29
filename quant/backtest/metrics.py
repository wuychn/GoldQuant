"""回测绩效指标：Sharpe/Sortino/Calmar、分年、出场归因、基准超额。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
import pandas as pd

from quant.backtest.stats import newey_west_sharpe


def _to_returns(equity: list[tuple[str, float]]) -> np.ndarray:
    if len(equity) < 2:
        return np.array([])
    vals = np.array([e[1] for e in equity], dtype=float)
    return vals[1:] / vals[:-1] - 1.0


def _max_drawdown(vals: np.ndarray) -> float:
    running_max = np.maximum.accumulate(vals)
    dd = (vals - running_max) / running_max
    return float(dd.min()) if len(dd) else 0.0


def exit_attribution(broker: SimBroker) -> dict[str, dict[str, Any]]:
    """按卖出 reason 分组的盈亏归因。"""
    groups: dict[str, list[float]] = defaultdict(list)
    for t in broker.trades:
        if t.side != "sell" or t.pnl is None:
            continue
        groups[t.reason or "unknown"].append(float(t.pnl))
    out: dict[str, dict[str, Any]] = {}
    for reason, pnls in sorted(groups.items()):
        arr = np.array(pnls, dtype=float)
        out[reason] = {
            "n": int(len(arr)),
            "total_pnl": round(float(arr.sum()), 2),
            "avg_pnl": round(float(arr.mean()), 2),
            "win_rate": round(float((arr > 0).mean()), 3),
        }
    return out


def yearly_breakdown(broker: SimBroker) -> dict[str, dict[str, float]]:
    """按自然年切分权益曲线收益。"""
    eq = broker.equity_curve
    if len(eq) < 2:
        return {}
    by_year: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for d, v in eq:
        by_year[str(d)[:4]].append((d, v))
    out: dict[str, dict[str, float]] = {}
    for y, pts in sorted(by_year.items()):
        if len(pts) < 2:
            continue
        ret = pts[-1][1] / pts[0][1] - 1.0
        rets = _to_returns(pts)
        vol = float(rets.std(ddof=1) * np.sqrt(252)) if len(rets) > 1 else 0.0
        out[y] = {
            "return_pct": round(ret * 100, 2),
            "ann_vol_pct": round(vol * 100, 2),
            "n_days": len(pts),
        }
    return out


def benchmark_excess(
    broker: SimBroker,
    benchmark: pd.DataFrame | None = None,
    *,
    code: str = "000300",
) -> dict[str, float]:
    """相对基准超额（总收益差 + 信息比率近似）。

    ``benchmark`` 需含 date/close；缺省时尝试读离线指数库。
    """
    _EMPTY_BENCH = {
        "excess_return_pct": 0.0,
        "info_ratio": 0.0,
        "bench_return_pct": 0.0,
        "beta": 0.0,
        "tracking_error_pct": 0.0,
        "relative_max_drawdown_pct": 0.0,
    }
    eq = broker.equity_curve
    if len(eq) < 2:
        return dict(_EMPTY_BENCH)
    if benchmark is None or benchmark.empty:
        try:
            from quant.data.store import read_index_daily

            start, end = eq[0][0], eq[-1][0]
            benchmark = read_index_daily(code, start=start, end=end)
        except Exception:
            benchmark = pd.DataFrame()
    if benchmark is None or benchmark.empty or "close" not in benchmark.columns:
        return dict(_EMPTY_BENCH)

    b = benchmark.copy()
    b["date"] = b["date"].astype(str).str.slice(0, 10)
    b = b.sort_values("date")
    bmap = dict(zip(b["date"], pd.to_numeric(b["close"], errors="coerce")))
    aligned_s: list[float] = []
    aligned_b: list[float] = []
    for d, v in eq:
        bc = bmap.get(str(d)[:10])
        if bc is None or not np.isfinite(bc) or bc <= 0:
            continue
        aligned_s.append(float(v))
        aligned_b.append(float(bc))
    empty = dict(_EMPTY_BENCH)
    if len(aligned_s) < 2:
        return empty
    s = np.array(aligned_s)
    bb = np.array(aligned_b)
    strat_ret = s[-1] / s[0] - 1.0
    bench_ret = bb[-1] / bb[0] - 1.0
    s_rets = s[1:] / s[:-1] - 1.0
    b_rets = bb[1:] / bb[:-1] - 1.0
    excess = s_rets - b_rets
    ir = 0.0
    te = 0.0
    beta = 0.0
    if len(excess) > 1:
        sd = float(excess.std(ddof=1))
        if sd > 1e-12:
            ir = float(excess.mean() / sd * np.sqrt(252))
            te = float(sd * np.sqrt(252) * 100)
        b_var = float(b_rets.var(ddof=1))
        if b_var > 1e-12:
            beta = float(np.cov(s_rets, b_rets)[0, 1] / b_var)
    rel_curve = s / bb
    rel_mdd = _max_drawdown(rel_curve) * 100
    return {
        "excess_return_pct": round((strat_ret - bench_ret) * 100, 2),
        "bench_return_pct": round(bench_ret * 100, 2),
        "info_ratio": round(ir, 3),
        "beta": round(beta, 3),
        "tracking_error_pct": round(te, 2),
        "relative_max_drawdown_pct": round(rel_mdd, 2),
    }


def compute_metrics(
    broker: SimBroker,
    *,
    trading_days: int = 252,
    benchmark: pd.DataFrame | None = None,
    benchmark_code: str = "000300",
    daily: pd.DataFrame | None = None,
    initial_cash: float = 1_000_000.0,
    participation_rate: float = 0.1,
) -> dict[str, Any]:
    eq = broker.equity_curve
    empty = {
        "total_return_pct": 0.0,
        "ann_return_pct": 0.0,
        "ann_vol_pct": 0.0,
        "sharpe": 0.0,
        "sharpe_nw": 0.0,
        "sortino": 0.0,
        "max_drawdown_pct": 0.0,
        "calmar": 0.0,
        "turnover_annual": 0.0,
        "turnover_buy_annual": 0.0,
        "turnover_sell_annual": 0.0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "avg_hold_days": 0.0,
        "n_trades": 0,
        "n_days": len(eq),
        "exit_attribution": {},
        "yearly": {},
        "excess_return_pct": 0.0,
        "bench_return_pct": 0.0,
        "info_ratio": 0.0,
        "beta": 0.0,
        "tracking_error_pct": 0.0,
        "relative_max_drawdown_pct": 0.0,
    }
    if len(eq) < 2:
        return empty

    rets = _to_returns(eq)
    vals = np.array([e[1] for e in eq], dtype=float)
    total_ret = vals[-1] / vals[0] - 1.0
    n = len(rets)
    ann_ret = (1 + total_ret) ** (trading_days / max(n, 1)) - 1.0
    ann_vol = float(rets.std(ddof=1) * np.sqrt(trading_days)) if n > 1 else 0.0
    sharpe = (ann_ret / ann_vol) if ann_vol > 1e-12 else 0.0
    sharpe_nw = newey_west_sharpe(rets, trading_days=trading_days)

    downside = rets[rets < 0]
    down_vol = float(downside.std(ddof=1) * np.sqrt(trading_days)) if len(downside) > 1 else 0.0
    sortino = (ann_ret / down_vol) if down_vol > 1e-12 else 0.0

    max_dd = _max_drawdown(vals)
    calmar = ann_ret / abs(max_dd) if abs(max_dd) > 1e-12 else 0.0

    avg_eq = float(vals.mean()) or 1.0
    buy_turn = sum(t.price * t.shares for t in broker.trades if t.side == "buy")
    sell_turn = sum(t.price * t.shares for t in broker.trades if t.side == "sell")
    total_turnover = buy_turn + sell_turn
    turnover_annual = (total_turnover / avg_eq) * (trading_days / max(n, 1))
    turnover_buy_annual = (buy_turn / avg_eq) * (trading_days / max(n, 1))
    turnover_sell_annual = (sell_turn / avg_eq) * (trading_days / max(n, 1))

    sells = [t for t in broker.trades if t.side == "sell" and t.pnl is not None]
    win = sum(1 for t in sells if t.pnl > 0)
    win_rate = win / len(sells) if sells else 0.0
    gains = sum(t.pnl for t in sells if t.pnl > 0)
    losses = abs(sum(t.pnl for t in sells if t.pnl < 0))
    profit_factor = (gains / losses) if losses > 1e-9 else (float("inf") if gains > 0 else 0.0)

    # 平均持仓天数：用买卖配对的粗略估计（FIFO 不严格，按买卖间隔均值）
    buy_dates: dict[str, list[str]] = defaultdict(list)
    hold_days: list[int] = []
    date_idx = {d: i for i, (d, _) in enumerate(eq)}
    for t in broker.trades:
        if t.side == "buy":
            buy_dates[t.code].append(t.date)
        elif t.side == "sell" and buy_dates.get(t.code):
            bd = buy_dates[t.code].pop(0)
            if bd in date_idx and t.date in date_idx:
                hold_days.append(date_idx[t.date] - date_idx[bd])

    excess = benchmark_excess(broker, benchmark, code=benchmark_code)

    from quant.backtest.attribution import capacity_metrics, style_attribution

    style = style_attribution(broker, daily, benchmark=benchmark, benchmark_code=benchmark_code)
    capacity = capacity_metrics(
        broker, daily, initial_cash=initial_cash, participation_rate=participation_rate
    )

    return {
        "total_return_pct": round(total_ret * 100, 2),
        "ann_return_pct": round(ann_ret * 100, 2),
        "ann_vol_pct": round(ann_vol * 100, 2),
        "sharpe": round(sharpe, 3),
        "sharpe_nw": round(sharpe_nw, 3),
        "sortino": round(sortino, 3),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "calmar": round(calmar, 3),
        "turnover_annual": round(turnover_annual, 2),
        "turnover_buy_annual": round(turnover_buy_annual, 2),
        "turnover_sell_annual": round(turnover_sell_annual, 2),
        "win_rate": round(win_rate, 3),
        "profit_factor": round(float(profit_factor), 3) if profit_factor != float("inf") else 999.0,
        "avg_hold_days": round(float(np.mean(hold_days)), 1) if hold_days else 0.0,
        "n_trades": len(broker.trades),
        "n_days": len(eq),
        "final_equity": round(float(vals[-1]), 2),
        "exit_attribution": exit_attribution(broker),
        "yearly": yearly_breakdown(broker),
        "style_attribution": style,
        "capacity": capacity,
        **excess,
    }
