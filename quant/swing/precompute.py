"""更快的买入预计算：pullback / momentum 两种模式。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant.exit.atr import atr
from quant.swing.signals import SwingBandParams, passes_position_pctile


def precompute_buy_cache(
    daily: pd.DataFrame,
    dates: list[str],
    params: SwingBandParams,
) -> dict[str, list[tuple[str, float]]]:
    mode = (params.buy_mode or "pullback").lower()
    if mode == "momentum":
        return _precompute_momentum(daily, dates, params)
    if mode == "rank_mom":
        return _precompute_rank_mom(daily, dates, params)
    if mode == "rank_rev":
        # 复用 rank_mom 特征后取负排序
        raw = _precompute_rank_mom(daily, dates, params)
        out = {}
        for dt, lst in raw.items():
            inv = [(c, -float(s)) for c, s in lst]
            inv.sort(key=lambda x: -x[1])
            out[dt] = inv
        return out
    return _precompute_pullback(daily, dates, params)


def _base_setup(daily: pd.DataFrame, dates: list[str], params: SwingBandParams, min_len: int):
    d = daily.copy()
    if not pd.api.types.is_string_dtype(d["date"]):
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
    d["code"] = d["code"].astype(str)
    date_set = set(dates)
    dist_by_date: dict[str, list[float]] = {dt: [] for dt in dates}
    feats: dict[str, list[tuple[str, float, float]]] = {dt: [] for dt in dates}
    groups = list(d.groupby("code", sort=False))
    return d, date_set, dist_by_date, feats, groups, min_len


def _finalize(dates, dist_by_date, feats, pctile):
    out: dict[str, list[tuple[str, float]]] = {}
    for dt in dates:
        dists = dist_by_date.get(dt) or []
        kept = [
            (c, s)
            for c, s, dh in feats.get(dt) or []
            if passes_position_pctile(dh, dists, pctile)
        ]
        kept.sort(key=lambda x: -x[1])
        out[dt] = kept
    print(f"  swing precompute done days={len(dates)}", flush=True)
    return out


def _precompute_pullback(
    daily: pd.DataFrame,
    dates: list[str],
    params: SwingBandParams,
) -> dict[str, list[tuple[str, float]]]:
    min_len = max(
        params.n_lookback + 2,
        params.bounce_days + 2,
        params.atr_period + 2,
        params.ma_period + params.ma_slope_lookback + 1,
        30,
    )
    _, date_set, dist_by_date, feats, groups, min_len = _base_setup(daily, dates, params, min_len)
    n_codes = len(groups)
    for gi, (code, g) in enumerate(groups):
        if (gi + 1) % 500 == 0:
            print(f"  swing precompute {gi+1}/{n_codes}", flush=True)
        g = g.sort_values("date")
        if len(g) < min_len:
            continue
        dates_c = g["date"].astype(str).to_numpy()
        close = pd.to_numeric(g["close"], errors="coerce").to_numpy(dtype=float)
        high = pd.to_numeric(g["high"], errors="coerce").to_numpy(dtype=float)
        atr_s = atr(g, params.atr_period).to_numpy(dtype=float)

        for i in range(min_len - 1, len(g)):
            dt = dates_c[i]
            if dt not in date_set:
                continue
            last = close[i]
            a = atr_s[i]
            if not np.isfinite(last) or last <= 0 or not np.isfinite(a) or a <= 0:
                continue

            lo252 = max(0, i + 1 - params.dist_high_lookback)
            hi252 = float(np.nanmax(high[lo252 : i + 1]))
            if not np.isfinite(hi252) or hi252 <= 0:
                continue
            dh = last / hi252 - 1.0
            dist_by_date[dt].append(dh)

            if params.require_above_ma20:
                ma = float(np.nanmean(close[i + 1 - params.ma_period : i + 1]))
                if not np.isfinite(ma) or last < ma:
                    continue
            if params.require_ma_rising:
                ma_now = float(np.nanmean(close[i + 1 - params.ma_period : i + 1]))
                ma_prev = float(
                    np.nanmean(
                        close[
                            i
                            + 1
                            - params.ma_period
                            - params.ma_slope_lookback : i
                            + 1
                            - params.ma_slope_lookback
                        ]
                    )
                )
                if not (np.isfinite(ma_now) and np.isfinite(ma_prev) and ma_now > ma_prev):
                    continue

            lo_n = i + 1 - params.n_lookback
            peak = float(np.nanmax(high[lo_n : i + 1]))
            if not np.isfinite(peak) or peak <= 0:
                continue
            pullback = (peak - last) / a
            if pullback < params.pullback_atr_mult or pullback > params.pullback_atr_max:
                continue

            j_b = i - params.bounce_days
            if j_b < 0 or not np.isfinite(close[j_b]) or close[j_b] <= 0:
                continue
            bounce = last / close[j_b] - 1.0
            if bounce < params.bounce_atr_mult * (a / last):
                continue

            j_n = i - params.n_lookback
            if j_n < 0 or not np.isfinite(close[j_n]) or close[j_n] <= 0:
                continue
            run = last / close[j_n] - 1.0
            if run > params.max_run_atr_mult * (a / last):
                continue

            strength = float(min(pullback, params.pullback_atr_max) * (1.0 + bounce / max(a / last, 1e-9)))
            feats[dt].append((str(code), strength, dh))

    return _finalize(dates, dist_by_date, feats, params.dist_high_pctile)


def _precompute_rank_mom(
    daily: pd.DataFrame,
    dates: list[str],
    params: SwingBandParams,
) -> dict[str, list[tuple[str, float]]]:
    """全市场可排序强度（无动量门槛）；仍可用 dist_high 分位过滤。"""
    min_len = max(params.n_lookback + 2, params.atr_period + 2, params.ma_period + 2, 30)
    if params.require_ma_rising:
        min_len = max(min_len, params.ma_period + params.ma_slope_lookback + 1)
    _, date_set, dist_by_date, feats, groups, min_len = _base_setup(daily, dates, params, min_len)
    n_codes = len(groups)
    for gi, (code, g) in enumerate(groups):
        if (gi + 1) % 500 == 0:
            print(f"  swing precompute {gi+1}/{n_codes}", flush=True)
        g = g.sort_values("date")
        if len(g) < min_len:
            continue
        dates_c = g["date"].astype(str).to_numpy()
        close = pd.to_numeric(g["close"], errors="coerce").to_numpy(dtype=float)
        high = pd.to_numeric(g["high"], errors="coerce").to_numpy(dtype=float)
        atr_s = atr(g, params.atr_period).to_numpy(dtype=float)

        for i in range(min_len - 1, len(g)):
            dt = dates_c[i]
            if dt not in date_set:
                continue
            last = close[i]
            a = atr_s[i]
            if not np.isfinite(last) or last <= 0 or not np.isfinite(a) or a <= 0:
                continue

            lo252 = max(0, i + 1 - params.dist_high_lookback)
            hi252 = float(np.nanmax(high[lo252 : i + 1]))
            if not np.isfinite(hi252) or hi252 <= 0:
                continue
            dh = last / hi252 - 1.0
            dist_by_date[dt].append(dh)

            if params.require_above_ma20:
                ma = float(np.nanmean(close[i + 1 - params.ma_period : i + 1]))
                if not np.isfinite(ma) or last < ma:
                    continue
            if params.require_ma_rising:
                ma_now = float(np.nanmean(close[i + 1 - params.ma_period : i + 1]))
                ma_prev = float(
                    np.nanmean(
                        close[
                            i
                            + 1
                            - params.ma_period
                            - params.ma_slope_lookback : i
                            + 1
                            - params.ma_slope_lookback
                        ]
                    )
                )
                if not (np.isfinite(ma_now) and np.isfinite(ma_prev) and ma_now > ma_prev):
                    continue

            j_n = i - params.n_lookback
            if j_n < 0 or not np.isfinite(close[j_n]) or close[j_n] <= 0:
                continue
            run = last / close[j_n] - 1.0
            strength = float(run / max(a / last, 1e-9))
            feats[dt].append((str(code), strength, dh))

    return _finalize(dates, dist_by_date, feats, params.dist_high_pctile)


def _precompute_momentum(
    daily: pd.DataFrame,
    dates: list[str],
    params: SwingBandParams,
) -> dict[str, list[tuple[str, float]]]:
    min_len = max(params.n_lookback + 2, params.atr_period + 2, params.ma_period + 2, 30)
    if params.require_ma_rising:
        min_len = max(min_len, params.ma_period + params.ma_slope_lookback + 1)
    _, date_set, dist_by_date, feats, groups, min_len = _base_setup(daily, dates, params, min_len)
    n_codes = len(groups)
    for gi, (code, g) in enumerate(groups):
        if (gi + 1) % 500 == 0:
            print(f"  swing precompute {gi+1}/{n_codes}", flush=True)
        g = g.sort_values("date")
        if len(g) < min_len:
            continue
        dates_c = g["date"].astype(str).to_numpy()
        close = pd.to_numeric(g["close"], errors="coerce").to_numpy(dtype=float)
        high = pd.to_numeric(g["high"], errors="coerce").to_numpy(dtype=float)
        atr_s = atr(g, params.atr_period).to_numpy(dtype=float)

        for i in range(min_len - 1, len(g)):
            dt = dates_c[i]
            if dt not in date_set:
                continue
            last = close[i]
            a = atr_s[i]
            if not np.isfinite(last) or last <= 0 or not np.isfinite(a) or a <= 0:
                continue

            lo252 = max(0, i + 1 - params.dist_high_lookback)
            hi252 = float(np.nanmax(high[lo252 : i + 1]))
            if not np.isfinite(hi252) or hi252 <= 0:
                continue
            dh = last / hi252 - 1.0
            dist_by_date[dt].append(dh)

            if params.require_above_ma20:
                ma = float(np.nanmean(close[i + 1 - params.ma_period : i + 1]))
                if not np.isfinite(ma) or last < ma:
                    continue
            if params.require_ma_rising:
                ma_now = float(np.nanmean(close[i + 1 - params.ma_period : i + 1]))
                ma_prev = float(
                    np.nanmean(
                        close[
                            i
                            + 1
                            - params.ma_period
                            - params.ma_slope_lookback : i
                            + 1
                            - params.ma_slope_lookback
                        ]
                    )
                )
                if not (np.isfinite(ma_now) and np.isfinite(ma_prev) and ma_now > ma_prev):
                    continue

            j_n = i - params.n_lookback
            if j_n < 0 or not np.isfinite(close[j_n]) or close[j_n] <= 0:
                continue
            run = last / close[j_n] - 1.0
            thr = params.momentum_atr_mult * (a / last)
            if run < thr:
                continue
            strength = float(run / max(a / last, 1e-9))
            feats[dt].append((str(code), strength, dh))

    return _finalize(dates, dist_by_date, feats, params.dist_high_pctile)
