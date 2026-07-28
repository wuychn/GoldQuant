"""组合风格归因：策略日收益对 market/size/value/momentum 因子回归。"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from quant.backtest.broker import SimBroker


def _daily_returns(equity: list[tuple[str, float]]) -> tuple[list[str], np.ndarray]:
    if len(equity) < 2:
        return [], np.array([])
    dates = [e[0] for e in equity[1:]]
    vals = np.array([e[1] for e in equity], dtype=float)
    rets = vals[1:] / vals[:-1] - 1.0
    return dates, rets


def _style_factor_returns(
    daily: pd.DataFrame,
    dates: list[str],
) -> dict[str, np.ndarray]:
    """从全市场截面构造 size / value / momentum 因子日收益（PIT）。"""
    if daily.empty or not dates:
        return {"size": np.array([]), "value": np.array([]), "momentum": np.array([])}
    d = daily.copy()
    if not pd.api.types.is_string_dtype(d["date"]):
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
    d = d.sort_values(["code", "date"])
    size_rets: list[float] = []
    val_rets: list[float] = []
    mom_rets: list[float] = []
    from quant.data.fundamentals import read_fundamentals_snapshot

    fund_cache: dict[str, dict[str, dict[str, float]]] = {}
    by_code = {c: g.sort_values("date").reset_index(drop=True) for c, g in d.groupby("code")}
    for dt in dates:
        if dt not in fund_cache:
            fund_cache[dt] = read_fundamentals_snapshot(dt)
        ep_map = {
            c: (1.0 / v["pe_ttm"] if v.get("pe_ttm") and v["pe_ttm"] > 0 else v.get("bp"))
            for c, v in fund_cache[dt].items()
        }
        rows: list[tuple[float, float, float, float]] = []
        for code, g in by_code.items():
            sub = g[g["date"] <= dt]
            if len(sub) < 2:
                continue
            pos = sub[sub["date"] == dt]
            if pos.empty:
                continue
            prev = sub.iloc[-2]
            cur = sub.iloc[-1]
            try:
                c0 = float(prev["close"])
                c1 = float(cur["close"])
            except (TypeError, ValueError):
                continue
            if c0 <= 0 or c1 <= 0:
                continue
            ret = c1 / c0 - 1.0
            mv = float(cur.get("float_mv") or cur.get("total_mv") or 0)
            if mv <= 0:
                continue
            mom60 = np.nan
            if len(sub) >= 61:
                c_old = float(sub.iloc[-61]["close"])
                if c_old > 0:
                    mom60 = c1 / c_old - 1.0
            ep = ep_map.get(code)
            if ep is None or not np.isfinite(ep):
                ep = 1.0 / mv * 1e10  # 无基本面时用市值倒数作弱代理
            if mom60 == mom60:
                rows.append((mv, ret, float(mom60), float(ep)))
        if len(rows) < 20:
            size_rets.append(0.0)
            val_rets.append(0.0)
            mom_rets.append(0.0)
            continue
        mvs = np.array([x[0] for x in rows])
        rets = np.array([x[1] for x in rows])
        moms = np.array([x[2] for x in rows])
        eps = np.array([x[3] for x in rows])
        q = np.quantile(mvs, [0.2, 0.8])
        small = rets[mvs <= q[0]]
        large = rets[mvs >= q[1]]
        size_rets.append(float(small.mean() - large.mean()) if len(small) and len(large) else 0.0)
        vq = np.quantile(eps, [0.2, 0.8])
        high_v = rets[eps >= vq[1]]
        low_v = rets[eps <= vq[0]]
        val_rets.append(float(high_v.mean() - low_v.mean()) if len(high_v) and len(low_v) else 0.0)
        mq = np.quantile(moms, [0.2, 0.8])
        win = rets[moms >= mq[1]]
        lose = rets[moms <= mq[0]]
        mom_rets.append(float(win.mean() - lose.mean()) if len(win) and len(lose) else 0.0)
    return {"size": np.array(size_rets), "value": np.array(val_rets), "momentum": np.array(mom_rets)}


def style_attribution(
    broker: SimBroker,
    daily: pd.DataFrame | None = None,
    *,
    benchmark: pd.DataFrame | None = None,
    benchmark_code: str = "000300",
) -> dict[str, Any]:
    """OLS：策略日超额 ~ market + size + value + momentum。返回 alpha/beta/R²。"""
    dates, s_rets = _daily_returns(broker.equity_curve)
    empty = {
        "alpha_ann_pct": 0.0,
        "betas": {"market": 0.0, "size": 0.0, "value": 0.0, "momentum": 0.0},
        "r_squared": 0.0,
        "n_days": 0,
    }
    if len(s_rets) < 10:
        return empty

    b_rets = np.zeros(len(s_rets))
    if benchmark is not None and not benchmark.empty and "close" in benchmark.columns:
        b = benchmark.copy()
        b["date"] = b["date"].astype(str).str.slice(0, 10)
        b = b.sort_values("date")
        b["ret"] = pd.to_numeric(b["close"], errors="coerce").pct_change()
        bmap = dict(zip(b["date"], b["ret"]))
        b_rets = np.array([float(bmap.get(str(d)[:10]) or 0.0) for d in dates])
    elif daily is not None:
        try:
            from quant.data.store import read_index_daily

            start, end = dates[0], dates[-1]
            bench = read_index_daily(benchmark_code, start=start, end=end)
            if bench is not None and not bench.empty:
                return style_attribution(broker, daily, benchmark=bench, benchmark_code=benchmark_code)
        except Exception:
            pass

    style = _style_factor_returns(daily, dates) if daily is not None and not daily.empty else {
        "size": np.zeros(len(s_rets)), "value": np.zeros(len(s_rets)), "momentum": np.zeros(len(s_rets))
    }
    size_r = style["size"]
    val_r = style["value"]
    mom_r = style["momentum"]
    if len(size_r) != len(s_rets):
        size_r = np.zeros(len(s_rets))
    if len(val_r) != len(s_rets):
        val_r = np.zeros(len(s_rets))
    if len(mom_r) != len(s_rets):
        mom_r = np.zeros(len(s_rets))

    excess = s_rets - b_rets
    X = np.column_stack([np.ones(len(excess)), b_rets, size_r, val_r, mom_r])
    try:
        beta, _, _, _ = np.linalg.lstsq(X, excess, rcond=None)
    except Exception:
        return empty
    alpha_d = float(beta[0])
    y_hat = X @ beta
    ss_res = float(np.sum((excess - y_hat) ** 2))
    ss_tot = float(np.sum((excess - excess.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-18 else 0.0
    return {
        "alpha_ann_pct": round(alpha_d * 252 * 100, 2),
        "betas": {
            "market": round(float(beta[1]), 3),
            "size": round(float(beta[2]), 3),
            "value": round(float(beta[3]), 3),
            "momentum": round(float(beta[4]), 3),
        },
        "r_squared": round(r2, 3),
        "n_days": len(s_rets),
    }


def capacity_metrics(
    broker: SimBroker,
    daily: pd.DataFrame | None,
    *,
    initial_cash: float = 1_000_000.0,
    participation_rate: float = 0.1,
) -> dict[str, Any]:
    """容量诊断：成交参与率分布与建议最大 AUM。"""
    if not broker.trades or daily is None or daily.empty:
        return {
            "max_participation_pct": 0.0,
            "median_participation_pct": 0.0,
            "pct_trades_above_5pct_adv": 0.0,
            "suggested_max_aum": initial_cash,
            "n_buys": 0,
        }
    d = daily.copy()
    if not pd.api.types.is_string_dtype(d["date"]):
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
    adv_cache: dict[tuple[str, str], float] = {}
    parts: list[float] = []
    n_buys = 0
    for t in broker.trades:
        if t.side != "buy":
            continue
        n_buys += 1
        key = (t.code, t.date)
        if key not in adv_cache:
            sub = d[(d["code"] == t.code) & (d["date"] < t.date)].sort_values("date").tail(20)
            amt = pd.to_numeric(sub["amount"], errors="coerce").dropna()
            adv_cache[key] = float(amt.mean()) if not amt.empty else 0.0
        adv = adv_cache[key]
        notional = t.price * t.shares
        part = (notional / adv * 100.0) if adv > 1e-6 else 0.0
        parts.append(part)
    if not parts:
        return {
            "max_participation_pct": 0.0,
            "median_participation_pct": 0.0,
            "pct_trades_above_5pct_adv": 0.0,
            "suggested_max_aum": initial_cash,
            "n_buys": 0,
        }
    arr = np.array(parts)
    max_part = float(arr.max())
    med_part = float(np.median(arr))
    above5 = float((arr > 5.0).mean())
    # 若中位参与率 target=participation_rate*100，按比例缩放 AUM
    target_pct = participation_rate * 100.0
    scale = (target_pct / med_part) if med_part > 1e-6 else 1.0
    suggested = initial_cash * min(scale, 10.0) if med_part > target_pct else initial_cash * 10.0
    return {
        "max_participation_pct": round(max_part, 2),
        "median_participation_pct": round(med_part, 2),
        "pct_trades_above_5pct_adv": round(above5, 3),
        "suggested_max_aum": round(suggested, 0),
        "n_buys": n_buys,
    }
