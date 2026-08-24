"""波段买卖信号（纯函数）。

v3：趋势质量（MA20 上行）+ 回撤有上下限 + 反弹够 ATR；卖以转跌为主。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from quant.exit.atr import atr


@dataclass(frozen=True)
class SwingBandParams:
    # buy_mode: pullback=v3 回撤再起；momentum=设计稿 ATR 动量启动
    buy_mode: str = "pullback"
    n_lookback: int = 20
    atr_period: int = 14
    pullback_atr_mult: float = 0.8  # 至少回撤这么多
    pullback_atr_max: float = 2.5  # 回撤过深视为下跌中继，不买
    bounce_days: int = 3
    bounce_atr_mult: float = 0.3  # 近 bounce_days 收益 >= 该×(ATR/价)
    max_run_atr_mult: float = 2.0  # 反追高（比 v2 更严）
    require_ma_rising: bool = True  # MA20 上行
    ma_slope_lookback: int = 10  # 比较 MA20(t) vs MA20(t-k)
    dist_high_pctile: float = 50.0
    require_above_ma20: bool = True
    ma_period: int = 20
    trail_atr_mult: float = 3.5
    stall_enabled: bool = False
    stall_hold_days: int = 30
    stall_atr_mult: float = 0.5
    time_stop_days: int = 45
    dist_high_lookback: int = 252
    momentum_atr_mult: float = 1.5  # momentum 模式：近 N 日涨幅 ≥ a×(ATR/价)


def _ensure_date_str(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    if not pd.api.types.is_string_dtype(df["date"]):
        out = df.copy()
        out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
        return out
    return df


def last_atr(df: pd.DataFrame, period: int = 14) -> float | None:
    if df is None or len(df) < period + 1:
        return None
    a = float(atr(df, period).iloc[-1])
    return a if np.isfinite(a) and a > 0 else None


def ret_n(df: pd.DataFrame, n: int) -> float | None:
    c = pd.to_numeric(df["close"], errors="coerce")
    if len(c) < n + 1:
        return None
    a, b = float(c.iloc[-1]), float(c.iloc[-n - 1])
    if b <= 0 or not np.isfinite(a) or not np.isfinite(b):
        return None
    return a / b - 1.0


def dist_high(df: pd.DataFrame, lookback: int = 252) -> float | None:
    if df is None or df.empty:
        return None
    h = pd.to_numeric(df["high"], errors="coerce")
    c = pd.to_numeric(df["close"], errors="coerce")
    window = h.iloc[-min(len(h), lookback) :]
    hi = float(window.max())
    last = float(c.iloc[-1])
    if hi <= 0 or not np.isfinite(hi) or not np.isfinite(last):
        return None
    return last / hi - 1.0


def above_ma(df: pd.DataFrame, period: int = 20) -> bool:
    c = pd.to_numeric(df["close"], errors="coerce")
    if len(c) < period:
        return False
    ma = float(c.iloc[-period:].mean())
    last = float(c.iloc[-1])
    return np.isfinite(ma) and np.isfinite(last) and last >= ma


def ma_rising(df: pd.DataFrame, period: int = 20, lookback: int = 10) -> bool:
    c = pd.to_numeric(df["close"], errors="coerce")
    need = period + lookback
    if len(c) < need:
        return False
    ma_now = float(c.iloc[-period:].mean())
    ma_prev = float(c.iloc[-period - lookback : -lookback].mean())
    return np.isfinite(ma_now) and np.isfinite(ma_prev) and ma_now > ma_prev


def buy_strength(df: pd.DataFrame, params: SwingBandParams) -> float | None:
    """买入强度；``buy_mode=pullback|momentum|rank_mom|rank_rev``。"""
    mode = (params.buy_mode or "pullback").lower()
    if mode == "momentum":
        return _buy_strength_momentum(df, params)
    if mode == "rank_mom":
        return _buy_strength_rank_mom(df, params)
    if mode == "rank_rev":
        s = _buy_strength_rank_mom(df, params)
        return None if s is None else float(-s)
    return _buy_strength_pullback(df, params)


def _buy_strength_rank_mom(df: pd.DataFrame, params: SwingBandParams) -> float | None:
    """截面排名用：不设动量门槛，只算近 N 日涨幅/ATR 强度（可叠均线过滤）。"""
    df = _ensure_date_str(df)
    if df is None or df.empty:
        return None
    need = max(params.n_lookback, params.ma_period, params.atr_period + 1) + 2
    if len(df) < need:
        return None
    a = last_atr(df, params.atr_period)
    if a is None:
        return None
    close = pd.to_numeric(df["close"], errors="coerce")
    last = float(close.iloc[-1])
    if last <= 0 or not np.isfinite(last):
        return None
    if params.require_above_ma20 and not above_ma(df, params.ma_period):
        return None
    if params.require_ma_rising and not ma_rising(df, params.ma_period, params.ma_slope_lookback):
        return None
    run = ret_n(df, params.n_lookback)
    if run is None:
        return None
    return float(run / max(a / last, 1e-9))


def _buy_strength_momentum(df: pd.DataFrame, params: SwingBandParams) -> float | None:
    """设计稿：近 N 日涨幅 ≥ a×ATR（相对价），且位置/均线过滤。"""
    df = _ensure_date_str(df)
    if df is None or df.empty:
        return None
    need = max(params.n_lookback, params.ma_period, params.atr_period + 1) + 2
    if len(df) < need:
        return None
    a = last_atr(df, params.atr_period)
    if a is None:
        return None
    close = pd.to_numeric(df["close"], errors="coerce")
    last = float(close.iloc[-1])
    if last <= 0 or not np.isfinite(last):
        return None
    if params.require_above_ma20 and not above_ma(df, params.ma_period):
        return None
    if params.require_ma_rising and not ma_rising(df, params.ma_period, params.ma_slope_lookback):
        return None
    run = ret_n(df, params.n_lookback)
    if run is None:
        return None
    thr = params.momentum_atr_mult * (a / last)
    if run < thr:
        return None
    # 强度 = 涨幅 / (ATR/价)
    return float(run / max(a / last, 1e-9))


def _buy_strength_pullback(df: pd.DataFrame, params: SwingBandParams) -> float | None:
    """趋势内有限回撤后再起；过深回撤/已高潮/均线下行 → 拒绝。"""
    df = _ensure_date_str(df)
    if df is None or df.empty:
        return None
    need = (
        max(
            params.n_lookback,
            params.bounce_days,
            params.ma_period + params.ma_slope_lookback,
            params.atr_period + 1,
        )
        + 2
    )
    if len(df) < need:
        return None
    a = last_atr(df, params.atr_period)
    if a is None:
        return None
    close = pd.to_numeric(df["close"], errors="coerce")
    high = pd.to_numeric(df["high"], errors="coerce")
    last = float(close.iloc[-1])
    if last <= 0 or not np.isfinite(last):
        return None
    if params.require_above_ma20 and not above_ma(df, params.ma_period):
        return None
    if params.require_ma_rising and not ma_rising(df, params.ma_period, params.ma_slope_lookback):
        return None

    win_h = high.iloc[-params.n_lookback :]
    peak = float(win_h.max())
    if not np.isfinite(peak) or peak <= 0:
        return None
    pullback = (peak - last) / a
    if pullback < params.pullback_atr_mult:
        return None
    if pullback > params.pullback_atr_max:
        return None

    bounce = ret_n(df, params.bounce_days)
    bounce_thr = params.bounce_atr_mult * (a / last)
    if bounce is None or bounce < bounce_thr:
        return None

    run = ret_n(df, params.n_lookback)
    if run is None:
        return None
    max_run = params.max_run_atr_mult * (a / last)
    if run > max_run:
        return None

    # 强度：适中回撤 + 够强反弹
    return float(min(pullback, params.pullback_atr_max) * (1.0 + bounce / max(a / last, 1e-9)))


def passes_position_pctile(
    dist: float | None,
    all_dists: list[float],
    pctile: float,
) -> bool:
    if dist is None or not all_dists:
        return False
    arr = np.asarray(all_dists, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) < 5:
        return False
    cutoff = float(np.nanpercentile(arr, pctile))
    return float(dist) >= cutoff


@dataclass
class SellReason:
    reason: str  # trail | stall | time


def evaluate_sell(
    df: pd.DataFrame,
    *,
    entry_price: float,
    highest_close: float,
    hold_days: int,
    params: SwingBandParams,
) -> SellReason | None:
    df = _ensure_date_str(df)
    if df is None or df.empty or entry_price <= 0:
        return None
    last = float(pd.to_numeric(df["close"], errors="coerce").iloc[-1])
    if not np.isfinite(last):
        return None
    a = last_atr(df, params.atr_period)
    if a is not None and last <= float(highest_close) - params.trail_atr_mult * a:
        return SellReason("trail")
    ret = last / entry_price - 1.0
    if params.stall_enabled and hold_days >= params.stall_hold_days and a is not None:
        stall_thr = params.stall_atr_mult * (a / entry_price)
        if ret < stall_thr:
            return SellReason("stall")
    if hold_days >= params.time_stop_days and ret < 0:
        return SellReason("time")
    return None
