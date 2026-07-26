"""出场规则：ATR 跟踪 + 硬止损（ATR 与固定%取较紧）+ 趋势止损（连续2日破MA）+ 时间止损。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from quant.exit.atr import atr


@dataclass
class ExitSignal:
    reason: str
    price: float


def atr_trailing_stop(
    df: pd.DataFrame,
    *,
    entry_price: float,
    highest_close: float,
    atr_mult: float = 3.0,
    period: int = 14,
) -> ExitSignal | None:
    if len(df) < period + 1:
        return None
    a = atr(df, period).iloc[-1]
    if not np.isfinite(a) or a <= 0:
        return None
    stop = max(entry_price, highest_close) - atr_mult * a
    last = float(df["close"].iloc[-1])
    if last <= stop:
        return ExitSignal("atr_trailing", float(stop))
    return None


def hard_stop(
    df: pd.DataFrame,
    *,
    entry_price: float,
    stop_pct: float = 0.08,
    atr_mult_stop: float | None = 2.0,
    atr_period: int = 14,
) -> ExitSignal | None:
    """硬止损：``买入价 - atr_mult_stop×ATR`` 与 ``-stop_pct`` 取较紧者。"""
    stops = [entry_price * (1 - stop_pct)]
    if atr_mult_stop is not None and len(df) >= atr_period + 1:
        a = atr(df, atr_period).iloc[-1]
        if np.isfinite(a) and a > 0:
            stops.append(entry_price - atr_mult_stop * float(a))
    stop = max(stops)  # 取较紧 = 更高的止损线
    last = float(df["close"].iloc[-1])
    if last <= stop:
        return ExitSignal("hard_stop", float(stop))
    return None


def trend_stop(
    df: pd.DataFrame, *, ma_period: int = 20, consecutive: int = 2
) -> ExitSignal | None:
    """趋势止损：收盘价连续 ``consecutive`` 日低于 MA。"""
    if len(df) < ma_period + consecutive - 1:
        return None
    close = pd.to_numeric(df["close"], errors="coerce")
    ma = close.rolling(ma_period).mean()
    below = (close < ma).iloc[-consecutive:]
    if len(below) < consecutive or not bool(below.all()):
        return None
    last_ma = float(ma.iloc[-1])
    if not np.isfinite(last_ma):
        return None
    return ExitSignal("trend_stop_ma20", last_ma)


def time_stop(
    buy_date: str,
    as_of: str,
    *,
    max_hold_days: int = 20,
    calendar_fn=None,
    entry_price: float | None = None,
    last_price: float | None = None,
    require_unprofitable: bool = True,
) -> ExitSignal | None:
    """时间止损：持有超过 max_hold_days 交易日；默认要求浮盈 < 0 才触发。"""
    if calendar_fn is not None:
        days = calendar_fn(buy_date, as_of)
    else:
        a = _coerce_date(buy_date)
        b = _coerce_date(as_of)
        if a is None or b is None:
            return None
        days = (b - a).days
    if days < max_hold_days:
        return None
    if require_unprofitable and entry_price and last_price is not None:
        if last_price >= entry_price:
            return None
    return ExitSignal("time_stop", 0.0)


def _coerce_date(s):
    from datetime import date as _date

    s = str(s).strip()[:10]
    try:
        if len(s) == 8 and s.isdigit():
            return _date(int(s[:4]), int(s[4:6]), int(s[6:]))
        return _date.fromisoformat(s)
    except Exception:
        return None


def evaluate_exits(
    df: pd.DataFrame,
    *,
    entry_price: float,
    highest_close: float,
    buy_date: str,
    as_of: str,
    atr_mult: float = 3.0,
    atr_mult_stop: float = 2.0,
    hard_pct: float = 0.08,
    max_hold_days: int = 20,
    calendar_fn=None,
) -> ExitSignal | None:
    last = float(pd.to_numeric(df["close"], errors="coerce").iloc[-1]) if len(df) else 0.0
    candidates = [
        hard_stop(
            df,
            entry_price=entry_price,
            stop_pct=hard_pct,
            atr_mult_stop=atr_mult_stop,
        ),
        atr_trailing_stop(
            df,
            entry_price=entry_price,
            highest_close=highest_close,
            atr_mult=atr_mult,
        ),
        trend_stop(df, ma_period=20, consecutive=2),
        time_stop(
            buy_date,
            as_of,
            max_hold_days=max_hold_days,
            calendar_fn=calendar_fn,
            entry_price=entry_price,
            last_price=last,
            require_unprofitable=True,
        ),
    ]
    priority = {"hard_stop": 0, "atr_trailing": 1, "trend_stop_ma20": 2, "time_stop": 3}
    triggered = [c for c in candidates if c is not None]
    if not triggered:
        return None
    triggered.sort(key=lambda c: priority.get(c.reason, 9))
    return triggered[0]
