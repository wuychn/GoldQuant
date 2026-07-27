"""盘中择时日频代理回测测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant.backtest2.intraday_timing import run_intraday_timing_backtest, vol_ratio_from_history


def _synth(strong: bool, code: str, n: int = 60) -> pd.DataFrame:
    """强势票：close>open、放量、上行趋势；弱势票反之。"""
    dates = pd.bdate_range(end="2024-12-31", periods=n).strftime("%Y-%m-%d")
    drift = 0.01 if strong else -0.01
    rows = []
    base = 10.0
    for i, d in enumerate(dates):
        close = base * (1.0 + drift * i)
        open_ = close * (0.99 if strong else 1.01)  # 强：close>open；弱：close<open
        high = max(close, open_) * 1.005
        low = min(close, open_) * 0.995
        vol = (2e6 if strong else 5e5) * (1.0 + (0.02 if strong else -0.01) * i)  # 强放量、弱缩量
        rows.append({
            "code": code, "date": d, "open": open_, "high": high, "low": low,
            "close": close, "volume": vol, "amount": vol * close, "turnover_rate": 2.0 if strong else 0.5,
        })
    return pd.DataFrame(rows)


def test_vol_ratio_from_history():
    s = pd.Series([1e6, 1e6, 1e6, 1e6, 1e6, 3e6])  # 今日 3e6 / 前5日均 1e6 = 3.0
    assert abs(vol_ratio_from_history(s) - 3.0) < 1e-6
    assert vol_ratio_from_history(pd.Series([1e6])) == 0.0


def test_theta_timing_beats_pool():
    """强势盘中代码被 θ 触发，触发组收益 > 池内均值（edge>0）。"""
    frames = [_synth(True, f"60000{i}") for i in range(5)] + [_synth(False, f"00000{i}") for i in range(5)]
    daily = pd.concat(frames, ignore_index=True)
    dates = sorted(daily["date"].unique())[-30:]
    # alpha 全等：作战池=全部 10 只，区分只靠盘中择时
    alpha_by_date = {d: {c: 1.0 for c in daily["code"].unique()} for d in dates}

    res = run_intraday_timing_backtest(
        daily=daily, dates=dates, alpha_by_date=alpha_by_date,
        theta=0.5, pool_size=10, horizon=5,
    )
    assert res["n_trigger_days"] > 0, "应有触发日"
    assert res["triggered"]["n"] > 0
    # 触发组（强势）均值应高于池内均值
    assert res["edge"] > 0, res
    assert res["triggered"]["mean_ret"] > res["pool_all"]["mean_ret"]


def test_no_trigger_when_theta_too_high():
    """θ 过高 → 无触发，edge=0。"""
    frames = [_synth(True, f"60000{i}") for i in range(5)] + [_synth(False, f"00000{i}") for i in range(5)]
    daily = pd.concat(frames, ignore_index=True)
    dates = sorted(daily["date"].unique())[-20:]
    alpha_by_date = {d: {c: 1.0 for c in daily["code"].unique()} for d in dates}
    res = run_intraday_timing_backtest(
        daily=daily, dates=dates, alpha_by_date=alpha_by_date,
        theta=100.0, pool_size=10, horizon=5,
    )
    assert res["n_trigger_days"] == 0
    assert res["triggered"]["n"] == 0
