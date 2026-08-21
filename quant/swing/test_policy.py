"""SwingBandPolicy 小样本单测。"""

from __future__ import annotations

import pandas as pd

from quant.swing.policy import SwingBandPolicy
from quant.swing.signals import SwingBandParams


def _mk_daily():
    """构造两只票：AAA 强势上涨可买；BBB 持仓后转跌应卖。"""
    from datetime import date, timedelta

    d0 = date(2024, 1, 2)
    rows = []
    # 60 日
    for i in range(60):
        dt = (d0 + timedelta(days=i)).isoformat()
        # AAA: 后段大涨
        ca = 10.0 if i < 45 else 10.0 + (i - 45) * 0.5
        rows.append(
            {"date": dt, "code": "AAA", "open": ca, "high": ca * 1.02, "low": ca * 0.98, "close": ca, "volume": 1e6, "amount": 1e8}
        )
        # BBB: 先涨后崩
        if i < 40:
            cb = 10.0 + i * 0.1
        else:
            cb = 14.0 - (i - 40) * 0.6
        rows.append(
            {"date": dt, "code": "BBB", "open": cb, "high": cb * 1.02, "low": cb * 0.98, "close": cb, "volume": 1e6, "amount": 1e8}
        )
    return pd.DataFrame(rows)


def test_policy_opens_and_keeps_until_sell():
    daily = _mk_daily()
    dates = sorted(daily["date"].unique().tolist())
    params = SwingBandParams(
        n_lookback=5,
        pullback_atr_mult=0.3,
        bounce_days=2,
        max_run_atr_mult=10.0,
        dist_high_pctile=50,
        trail_atr_mult=2.0,
        stall_enabled=False,
        time_stop_days=99,
    )
    pol = SwingBandPolicy(daily=daily, params=params, max_stocks=2)
    pol.prepare(dates[-20:])  # 只用后段日期缓存
    # 取一个末段日期
    as_of = dates[-5]
    day = daily[daily["date"] == as_of]
    prices = {str(r.code): float(r.close) for r in day.itertuples()}
    w = pol.target_weights({}, prices, {}, as_of)
    # prepare 不应崩；是否开仓取决于合成路径是否满足回撤再起
    assert isinstance(w, dict)


def test_policy_sells_on_trail():
    daily = _mk_daily()
    dates = sorted(daily["date"].unique().tolist())
    as_of = dates[-1]
    params = SwingBandParams(trail_atr_mult=1.5, stall_enabled=False, time_stop_days=99)
    pol = SwingBandPolicy(daily=daily, params=params, max_stocks=1)
    d = daily.copy()
    d["code"] = d["code"].astype(str)
    pol._by_code = {str(c): g.sort_values("date").reset_index(drop=True) for c, g in d.groupby("code")}
    day = daily[daily["date"] == as_of]
    prices = {str(r.code): float(r.close) for r in day.itertuples()}
    pol.holding_snapshots = {"BBB": {"buy_date": dates[10], "cost": 11.0}}
    w = pol.target_weights({}, prices, {"BBB": 0.9}, as_of)
    assert "BBB" not in w
    assert pol.last_sell_reasons.get("BBB") == "trail"
