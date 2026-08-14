"""trend_state 单测。"""

from __future__ import annotations

import pandas as pd

from quant.portfolio.trend_state import trend_fail_streak, trend_ok


def _ohlc(closes: list[float], start="2024-01-02") -> pd.DataFrame:
    """构造足够长的假 K 线：high/low 略扩，便于 ATR。"""
    from datetime import date, timedelta

    d0 = date.fromisoformat(start)
    rows = []
    for i, c in enumerate(closes):
        dt = (d0 + timedelta(days=i)).isoformat()
        rows.append(
            {
                "date": dt,
                "open": c,
                "high": c * 1.01,
                "low": c * 0.99,
                "close": c,
                "volume": 1e6,
            }
        )
    return pd.DataFrame(rows)


def test_trend_ok_requires_ma_and_atr_band():
    # 25 日上行后小回撤，仍在 MA20 与 ATR 带内
    closes = [10 + i * 0.1 for i in range(25)]
    df = _ohlc(closes)
    hc = max(closes)
    assert trend_ok(df, highest_close=hc, atr_mult=3.0, ma_period=20) is True


def test_trend_ok_false_when_below_ma():
    closes = [10 + i * 0.1 for i in range(20)] + [8.0, 7.5, 7.0]
    df = _ohlc(closes)
    hc = max(closes)
    assert trend_ok(df, highest_close=hc, atr_mult=3.0, ma_period=20) is False


def test_trend_fail_streak_counts_consecutive():
    # 长升后连续跌破
    up = [10 + i * 0.2 for i in range(22)]
    down = [up[-1] - i * 0.8 for i in range(1, 4)]
    df = _ohlc(up + down)
    buy = str(df["date"].iloc[0])
    streak = trend_fail_streak(df, buy_date=buy, atr_mult=3.0, ma_period=20)
    assert streak >= 2
