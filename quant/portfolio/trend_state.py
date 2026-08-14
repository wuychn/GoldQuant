"""趋势状态：ATR 回撤带 + MA20，以及连续失败 streak。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant.exit.atr import atr


def trend_ok(
    df: pd.DataFrame,
    *,
    highest_close: float,
    atr_mult: float = 3.0,
    ma_period: int = 20,
    atr_period: int = 14,
) -> bool:
    """当日趋势是否仍 OK：收盘 ≥ 最高收盘−m×ATR 且 收盘 ≥ MA。"""
    if df is None or df.empty:
        return False
    close = pd.to_numeric(df["close"], errors="coerce")
    last = float(close.iloc[-1])
    if not np.isfinite(last):
        return False
    band_ok = True
    if len(df) >= atr_period + 1:
        a = float(atr(df, atr_period).iloc[-1])
        if np.isfinite(a) and a > 0:
            band_ok = last >= float(highest_close) - float(atr_mult) * a
    ma_ok = True
    if len(df) >= ma_period:
        ma = float(close.rolling(ma_period).mean().iloc[-1])
        if np.isfinite(ma):
            ma_ok = last >= ma
    return bool(band_ok and ma_ok)


def trend_fail_streak(
    df: pd.DataFrame,
    *,
    buy_date: str,
    atr_mult: float = 3.0,
    ma_period: int = 20,
    atr_period: int = 14,
    max_look: int = 30,
) -> int:
    """从 as_of（df 末行）向前数连续趋势失败交易日数。

    每日的 highest_close = buy_date 至该日的收盘最高价。
    """
    if df is None or df.empty:
        return 0
    d = df.copy()
    d["date"] = d["date"].astype(str).str.slice(0, 10)
    buy = str(buy_date)[:10]
    d = d[d["date"] >= buy].reset_index(drop=True)
    if d.empty:
        return 0
    n = len(d)
    start = max(0, n - max_look)
    streak = 0
    for i in range(n - 1, start - 1, -1):
        sub = d.iloc[: i + 1]
        hc = float(pd.to_numeric(sub["close"], errors="coerce").max())
        ok = trend_ok(
            sub,
            highest_close=hc,
            atr_mult=atr_mult,
            ma_period=ma_period,
            atr_period=atr_period,
        )
        if ok:
            break
        streak += 1
    return streak
