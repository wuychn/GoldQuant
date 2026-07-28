"""盘中择时日频代理回测（P1 剩余项）。

验证 T 晚作战池（alpha top-N）→ T+1 用日频代理 ``SpotRow`` 跑 ``compose_intraday_alpha``
→ α_z≥θ 触发买入 → 持有 ``horizon`` 日的**择时增益**：对比「触发组 vs 池内全部」的 forward
收益。若触发组均值显著高于池内均值（edge>0），说明 θ 择时有信息量；否则 θ 是噪声。

代理保真度见 ``factors.library.intraday.spot_row_from_daily``（``speed`` 不可得→中性）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def vol_ratio_from_history(vol_series: pd.Series) -> float:
    """今日量 / 近 5 日均量（不含今日）。不足返回 0。"""
    s = pd.to_numeric(vol_series, errors="coerce").dropna()
    if len(s) < 2:
        return 0.0
    prev = s.iloc[-6:-1] if len(s) >= 6 else s.iloc[:-1]
    if prev.empty:
        return 0.0
    m = float(prev.mean())
    return float(s.iloc[-1] / m) if m > 0 else 0.0


def run_intraday_timing_backtest(
    *,
    daily: pd.DataFrame,
    dates: list[str],
    alpha_by_date: dict[str, dict[str, float]],
    theta: float = 1.0,
    pool_size: int = 30,
    horizon: int = 5,
) -> dict:
    """日频代理盘中择时回测。返回触发组/池内全部的收益统计 + edge。"""
    from quant.factors.compose import compose_intraday_alpha
    from quant.factors.library.intraday import spot_row_from_daily

    df = daily.copy()
    df["date"] = df["date"].astype(str)
    df = df.sort_values(["code", "date"])
    close_w = df.pivot_table(index="date", columns="code", values="close", aggfunc="last").sort_index()
    vol_w = df.pivot_table(index="date", columns="code", values="volume", aggfunc="last").sort_index()
    row_by = {(str(r["date"]), str(r["code"])): r.to_dict() for _, r in df.iterrows()}
    iso_dates = list(close_w.index)
    date_idx = {d: i for i, d in enumerate(iso_dates)}

    trig_rets: list[float] = []
    pool_rets: list[float] = []
    n_trigger_days = 0

    for d in dates:
        i = date_idx.get(d)
        if i is None:
            continue
        alpha = alpha_by_date.get(d) or {}
        if not alpha:
            continue
        pool = [c for c, _ in sorted(alpha.items(), key=lambda kv: -kv[1])[:pool_size]]
        spot_rows = []
        for c in pool:
            row = row_by.get((d, c))
            if row is None:
                continue
            vr = vol_ratio_from_history(vol_w[c].loc[:d]) if c in vol_w else 0.0
            prev_close = close_w[c].iloc[i - 1] if (c in close_w and i > 0) else None
            sr = spot_row_from_daily(c, row, prev_close, vol_ratio=vr)
            if sr is not None:
                spot_rows.append(sr)
        if len(spot_rows) < 2:
            continue
        ia = compose_intraday_alpha(spot_rows)
        triggered = {c for c, z in ia.items() if z >= theta}
        for c in pool:
            if c not in close_w:
                continue
            closes = close_w[c]
            p0 = closes.get(d)
            j = i + horizon
            if p0 is None or pd.isna(p0) or p0 <= 0 or j >= len(iso_dates):
                continue
            p1 = closes.iloc[j]
            if pd.isna(p1) or p1 <= 0:
                continue
            ret = float(p1 / p0 - 1.0)
            pool_rets.append(ret)
            if c in triggered:
                trig_rets.append(ret)
        if triggered:
            n_trigger_days += 1

    def _stats(xs: list[float]) -> dict:
        if not xs:
            return {"n": 0, "mean_ret": 0.0, "hit_rate": 0.0}
        arr = np.array(xs, dtype=float)
        return {
            "n": int(len(arr)),
            "mean_ret": round(float(arr.mean()), 4),
            "hit_rate": round(float((arr > 0).mean()), 3),
        }

    s_trig = _stats(trig_rets)
    s_pool = _stats(pool_rets)
    return {
        "n_days": len(dates),
        "n_trigger_days": n_trigger_days,
        "theta": theta,
        "pool_size": pool_size,
        "horizon": horizon,
        "triggered": s_trig,
        "pool_all": s_pool,
        "edge": round(s_trig["mean_ret"] - s_pool["mean_ret"], 4),
    }
