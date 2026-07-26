"""出场规则：ATR 跟踪 + 硬止损 + 趋势止损 + 时间止损。

每条规则返回 ExitSignal(reason, suggested_price)。引擎择最严者执行。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from quant.exit.atr import atr


@dataclass
class ExitSignal:
    reason: str
    price: float  # 触发价（止损线）


def atr_trailing_stop(df: pd.DataFrame, *, entry_price: float, highest_close: float, atr_mult: float = 3.0, period: int = 20) -> ExitSignal | None:
    """ATR 跟踪止损：止盈线 = max(入场价, 持仓期最高收盘) - atr_mult * ATR。"""
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


def hard_stop(df: pd.DataFrame, *, entry_price: float, stop_pct: float = 0.08) -> ExitSignal | None:
    """硬止损：跌破入场价 * (1 - stop_pct)。"""
    stop = entry_price * (1 - stop_pct)
    last = float(df["close"].iloc[-1])
    if last <= stop:
        return ExitSignal("hard_stop", float(stop))
    return None


def trend_stop(df: pd.DataFrame, *, ma_period: int = 20) -> ExitSignal | None:
    """趋势止损：收盘跌破 MA20。"""
    if len(df) < ma_period:
        return None
    close = pd.to_numeric(df["close"], errors="coerce")
    ma = close.rolling(ma_period).mean().iloc[-1]
    last = float(close.iloc[-1])
    if not np.isfinite(ma):
        return None
    if last <= ma:
        return ExitSignal("trend_stop_ma20", float(ma))
    return None


def time_stop(buy_date: str, as_of: str, *, max_hold_days: int = 20, calendar_fn=None) -> ExitSignal | None:
    """时间止损：持有超过 max_hold_days 个交易日且未达主升。"""
    if calendar_fn is not None:
        days = calendar_fn(buy_date, as_of)
    else:
        # 回退：按日期差粗估
        from datetime import datetime

        a = datetime.strptime(buy_date, "%Y%m%d")
        b = datetime.strptime(as_of, "%Y%m%d")
        days = int((b - a).days)
    if days >= max_hold_days:
        return ExitSignal("time_stop", 0.0)
    return None


def evaluate_exits(
    df: pd.DataFrame,
    *,
    entry_price: float,
    highest_close: float,
    buy_date: str,
    as_of: str,
    atr_mult: float = 3.0,
    hard_pct: float = 0.08,
    max_hold_days: int = 20,
    calendar_fn=None,
) -> ExitSignal | None:
    """综合出场：返回最先触发的止损（按严重度排序：硬止损 > ATR跟踪 > 趋势 > 时间）。"""
    candidates = [
        hard_stop(df, entry_price=entry_price, stop_pct=hard_pct),
        atr_trailing_stop(df, entry_price=entry_price, highest_close=highest_close, atr_mult=atr_mult),
        trend_stop(df, ma_period=20),
        time_stop(buy_date, as_of, max_hold_days=max_hold_days, calendar_fn=calendar_fn),
    ]
    # 优先级：hard > atr > trend > time
    priority = {"hard_stop": 0, "atr_trailing": 1, "trend_stop_ma20": 2, "time_stop": 3}
    triggered = [c for c in candidates if c is not None]
    if not triggered:
        return None
    triggered.sort(key=lambda c: priority.get(c.reason, 9))
    return triggered[0]
