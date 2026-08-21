"""buy/sell 信号单测（v3）。"""

from __future__ import annotations

import pandas as pd

from quant.swing.signals import (
    SwingBandParams,
    buy_strength,
    dist_high,
    evaluate_sell,
    passes_position_pctile,
)


def _ohlc(closes: list[float], start="2024-01-02") -> pd.DataFrame:
    from datetime import date, timedelta

    d0 = date.fromisoformat(start)
    rows = []
    for i, c in enumerate(closes):
        rows.append(
            {
                "date": (d0 + timedelta(days=i)).isoformat(),
                "open": c,
                "high": c * 1.02,
                "low": c * 0.98,
                "close": c,
                "volume": 1e6,
            }
        )
    return pd.DataFrame(rows)


def test_buy_strength_rejects_deep_pullback():
    # 暴跌后小反弹：回撤过深应拒绝
    from datetime import date, timedelta

    d0 = date(2024, 1, 2)
    closes = [20.0 - i * 0.4 for i in range(30)] + [8.0, 8.1, 8.2]
    rows = []
    for i, c in enumerate(closes):
        rows.append(
            {
                "date": (d0 + timedelta(days=i)).isoformat(),
                "open": c,
                "high": c * 1.02,
                "low": c * 0.98,
                "close": c,
                "volume": 1e6,
            }
        )
    df = pd.DataFrame(rows)
    p = SwingBandParams(
        pullback_atr_mult=0.5,
        pullback_atr_max=2.0,
        bounce_atr_mult=0.01,
        require_ma_rising=False,
        require_above_ma20=False,
        max_run_atr_mult=20.0,
    )
    assert buy_strength(df, p) is None


def test_buy_strength_pullback_bounce():
    from datetime import date, timedelta

    d0 = date(2024, 1, 2)
    closes = []
    p = 10.0
    for _ in range(30):
        p *= 1.008
        closes.append(p)
    peak = p
    for i in range(5):
        p = peak * (1 - 0.015 * (i + 1))
        closes.append(p)
    for _ in range(3):
        p *= 1.02
        closes.append(p)
    rows = []
    for i, c in enumerate(closes):
        rows.append(
            {
                "date": (d0 + timedelta(days=i)).isoformat(),
                "open": c,
                "high": c * 1.03,
                "low": c * 0.97,
                "close": c,
                "volume": 1e6,
            }
        )
    df = pd.DataFrame(rows)
    params = SwingBandParams(
        n_lookback=20,
        pullback_atr_mult=0.3,
        pullback_atr_max=5.0,
        bounce_days=3,
        bounce_atr_mult=0.05,
        max_run_atr_mult=10.0,
        require_above_ma20=False,
        require_ma_rising=False,
    )
    s = buy_strength(df, params)
    assert s is not None and s > 0


def test_buy_strength_rejects_chase():
    df = _ohlc([10.0 + i * 0.5 for i in range(40)])
    p = SwingBandParams(
        n_lookback=20,
        pullback_atr_mult=0.8,
        bounce_days=3,
        max_run_atr_mult=1.0,
        require_above_ma20=True,
        require_ma_rising=False,
    )
    assert buy_strength(df, p) is None


def test_passes_position_pctile():
    dists = [-0.4, -0.3, -0.2, -0.1, -0.05, -0.02, -0.01]
    assert passes_position_pctile(-0.01, dists, 50.0) is True
    assert passes_position_pctile(-0.35, dists, 50.0) is False


def test_evaluate_sell_trail():
    up = [10 + i * 0.2 for i in range(20)]
    down = [up[-1] - i * 0.8 for i in range(1, 6)]
    df = _ohlc(up + down)
    p = SwingBandParams(trail_atr_mult=2.0, stall_enabled=False, time_stop_days=99)
    hc = max(up)
    sig = evaluate_sell(df, entry_price=10.0, highest_close=hc, hold_days=3, params=p)
    assert sig is not None and sig.reason == "trail"


def test_stall_disabled_by_default():
    df = _ohlc([10.0] * 30)
    p = SwingBandParams(stall_enabled=False, trail_atr_mult=99.0, time_stop_days=99)
    sig = evaluate_sell(df, entry_price=10.0, highest_close=10.0, hold_days=20, params=p)
    assert sig is None


def test_evaluate_sell_time_unprofitable():
    df = _ohlc([10.0 - i * 0.05 for i in range(50)])
    p = SwingBandParams(trail_atr_mult=99.0, stall_enabled=False, time_stop_days=45)
    sig = evaluate_sell(df, entry_price=10.0, highest_close=10.0, hold_days=45, params=p)
    assert sig is not None and sig.reason == "time"


def test_dist_high_near_peak():
    df = _ohlc([10 + i * 0.1 for i in range(30)])
    d = dist_high(df, 252)
    assert d is not None and d > -0.05
