"""stage_swing 单测。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant.swing.stage_swing import StageSwingParams, stage_buy_score, stage_sell


def _df_uptrend_pullback_bounce():
    """先上涨，再回调 1.5ATR 量级，再小反弹。"""
    rows = []
    p = 10.0
    # 上涨
    for i in range(80):
        p *= 1.01
        rows.append(
            {
                "date": f"2024-01-{(i%28)+1:02d}",
                "open": p,
                "high": p * 1.01,
                "low": p * 0.99,
                "close": p,
                "amount": 5e8,
                "name": "测试票",
            }
        )
    # 用连续日期
    from datetime import date, timedelta

    d0 = date(2024, 1, 2)
    rows = []
    p = 10.0
    for i in range(80):
        p *= 1.008
        rows.append(
            {
                "date": (d0 + timedelta(days=i)).isoformat(),
                "open": p,
                "high": p * 1.012,
                "low": p * 0.988,
                "close": p,
                "amount": 5e8,
                "name": "测试票",
            }
        )
    peak = p
    # 回调约 8%
    for i in range(5):
        p = peak * (1 - 0.02 * (i + 1))
        rows.append(
            {
                "date": (d0 + timedelta(days=80 + i)).isoformat(),
                "open": p,
                "high": p * 1.01,
                "low": p * 0.99,
                "close": p,
                "amount": 5e8,
                "name": "测试票",
            }
        )
    # 企稳反弹
    for i in range(3):
        p *= 1.015
        rows.append(
            {
                "date": (d0 + timedelta(days=85 + i)).isoformat(),
                "open": p,
                "high": p * 1.01,
                "low": p * 0.99,
                "close": p,
                "amount": 5e8,
                "name": "测试票",
            }
        )
    return pd.DataFrame(rows)


def test_buy_score_on_pullback_bounce():
    df = _df_uptrend_pullback_bounce()
    s = stage_buy_score(
        df,
        "测试票",
        StageSwingParams(
            pullback_atr_min=0.3,
            pullback_atr_max=8.0,
            bounce_atr_min=0.05,
            require_struct_ma_rising=False,
            require_above_struct_ma=False,
            require_above_ma20=False,
            require_close_up=False,
            require_break_pullback_high=False,
            min_stage_rise=0.0,
            peak_near_high_pct=0.90,
            min_adv=0.0,
        ),
    )
    assert s is not None


def test_buy_rejects_st():
    df = _df_uptrend_pullback_bounce()
    assert stage_buy_score(df, "ST测试", StageSwingParams()) is None


def test_thesis_fail_sell():
    from datetime import date, timedelta

    d0 = date(2024, 1, 2)
    rows = []
    for i in range(30):
        c = 10.0 - i * 0.05
        rows.append(
            {
                "date": (d0 + timedelta(days=i)).isoformat(),
                "open": c,
                "high": c * 1.01,
                "low": c * 0.99,
                "close": c,
                "amount": 5e8,
            }
        )
    df = pd.DataFrame(rows)
    reason = stage_sell(
        df,
        entry_price=10.0,
        highest_close=10.0,
        swing_low=9.5,
        hold_days=2,
        params=StageSwingParams(fail_atr_mult=0.5, fail_window_days=5),
    )
    assert reason == "thesis_fail"
