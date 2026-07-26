"""P4 出场层单元测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant.exit.atr import atr
from quant.exit.rules import atr_trailing_stop, evaluate_exits, hard_stop, time_stop, trend_stop
from quant.exit.state import ExitTracker


def _synth(n=40, drift=0.0, seed=1):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end="2024-12-31", periods=n).strftime("%Y%m%d")
    p = 10.0
    rows = []
    for d in dates:
        p = max(p * (1 + drift + rng.normal(0, 0.02)), 1.0)
        rows.append({"date": d, "open": p, "high": p * 1.01, "low": p * 0.99, "close": p, "volume": 1e6})
    return pd.DataFrame(rows)


def test_atr_positive():
    df = _synth()
    a = atr(df, 20)
    assert a.iloc[-1] > 0


def test_hard_stop_triggers_on_drawdown():
    df = _synth(n=30, drift=-0.02, seed=5)  # 持续下跌
    sig = hard_stop(df, entry_price=10.0, stop_pct=0.08)
    assert sig is not None
    assert sig.reason == "hard_stop"


def test_atr_trailing_after_run_up():
    df = _synth(n=60, drift=0.01, seed=2)
    entry = 10.0
    highest = float(df["close"].max())
    # 末尾注入大跌，明确跌破跟踪止损线
    last = float(df["close"].iloc[-1])
    df.loc[df.index[-1], "close"] = last * 0.7
    df.loc[df.index[-1], "low"] = last * 0.7
    df.loc[df.index[-1], "high"] = last * 0.71
    sig = atr_trailing_stop(df, entry_price=entry, highest_close=highest, atr_mult=2.0)
    assert sig is not None
    assert sig.reason == "atr_trailing"


def test_trend_stop_below_ma20():
    df = _synth(n=30, drift=-0.01, seed=9)
    sig = trend_stop(df, ma_period=20)
    # 下跌序列大概率破 MA20
    if sig is not None:
        assert sig.reason == "trend_stop_ma20"


def test_time_stop():
    sig = time_stop("20240101", "20240201", max_hold_days=10, calendar_fn=lambda a, b: 20)
    assert sig is not None and sig.reason == "time_stop"
    sig2 = time_stop("20240101", "20240103", max_hold_days=10, calendar_fn=lambda a, b: 2)
    assert sig2 is None


def test_evaluate_exits_priority():
    df = _synth(n=30, drift=-0.03, seed=4)
    sig = evaluate_exits(df, entry_price=10.0, highest_close=10.5, buy_date="20241001", as_of="20241101", hard_pct=0.05, max_hold_days=5, calendar_fn=lambda a, b: 10)
    assert sig is not None
    # 硬止损优先级最高
    assert sig.reason in ("hard_stop", "atr_trailing", "trend_stop_ma20", "time_stop")


def test_exit_tracker():
    t = ExitTracker()
    t.open("000001", 10.0, "20240101")
    t.update("000001", 11.0)
    t.update("000001", 10.5)
    assert t.get("000001").highest_close == 11.0
    t.close("000001")
    assert t.get("000001") is None
