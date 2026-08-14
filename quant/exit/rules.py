"""出场规则：ATR 跟踪 + 硬止损（ATR 与固定%取较紧）+ 趋势止损（连续2日破MA）+ 时间止损。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from quant.exit.atr import atr

# 出场默认参数（单一来源：rules / engine.ExitConfig / daily._build_sell_watch 共用）
# SwapGate 模式下 hard / atr 硬腿默认关，强制卖走趋势连续失败。
DEFAULT_ATR_MULT = 3.0
DEFAULT_ATR_MULT_STOP = 2.0
DEFAULT_HARD_PCT = 0.08
DEFAULT_MAX_HOLD_DAYS = 20


@dataclass
class ExitSignal:
    reason: str
    price: float


def trend_force_exit(
    df: pd.DataFrame,
    *,
    buy_date: str,
    atr_mult: float = 3.0,
    ma_period: int = 20,
    trend_fail_days: int = 2,
) -> ExitSignal | None:
    """与 SwapGate 同口径：ATR 带 + MA20 连续失败 → 强制卖。"""
    from quant.portfolio.trend_state import trend_fail_streak

    streak = trend_fail_streak(
        df,
        buy_date=buy_date,
        atr_mult=atr_mult,
        ma_period=ma_period,
    )
    if streak < trend_fail_days:
        return None
    last = float(pd.to_numeric(df["close"], errors="coerce").iloc[-1]) if len(df) else 0.0
    return ExitSignal("trend_force", last)


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
    atr_mult: float = DEFAULT_ATR_MULT,
    atr_mult_stop: float | None = DEFAULT_ATR_MULT_STOP,
    hard_pct: float | None = DEFAULT_HARD_PCT,
    max_hold_days: int = DEFAULT_MAX_HOLD_DAYS,
    calendar_fn=None,
    atr_trailing: bool = True,
    use_trend_force: bool = False,
    trend_fail_days: int = 2,
    ma_period: int = 20,
) -> ExitSignal | None:
    last = float(pd.to_numeric(df["close"], errors="coerce").iloc[-1]) if len(df) else 0.0
    hard_sig = None
    if hard_pct is not None or atr_mult_stop is not None:
        # %-腿关闭时用 0.99 占位，使 max(stops) 几乎只听 ATR 腿
        stop_pct = 0.99 if hard_pct is None else hard_pct
        hard_sig = hard_stop(
            df,
            entry_price=entry_price,
            stop_pct=stop_pct,
            atr_mult_stop=atr_mult_stop,
        )
    candidates = [hard_sig]
    if atr_trailing:
        candidates.append(
            atr_trailing_stop(
                df,
                entry_price=entry_price,
                highest_close=highest_close,
                atr_mult=atr_mult,
            )
        )
    if use_trend_force:
        candidates.append(
            trend_force_exit(
                df,
                buy_date=buy_date,
                atr_mult=atr_mult,
                ma_period=ma_period,
                trend_fail_days=trend_fail_days,
            )
        )
    else:
        candidates.append(trend_stop(df, ma_period=ma_period, consecutive=2))
    candidates.append(
        time_stop(
            buy_date,
            as_of,
            max_hold_days=max_hold_days,
            calendar_fn=calendar_fn,
            entry_price=entry_price,
            last_price=last,
            require_unprofitable=True,
        )
    )
    priority = {
        "hard_stop": 0,
        "atr_trailing": 1,
        "trend_force": 2,
        "trend_stop_ma20": 2,
        "time_stop": 3,
    }
    triggered = [c for c in candidates if c is not None]
    if not triggered:
        return None
    triggered.sort(key=lambda c: priority.get(c.reason, 9))
    return triggered[0]
