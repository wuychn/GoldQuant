"""入场动量过滤单测。"""

from __future__ import annotations

import pandas as pd

from quant.portfolio.entry_filters import filter_new_entries, overheat_codes


def _daily_hot_and_cold():
    # a: 5 日 +20%；b: 5 日 +5%；c: 持平
    rows = []
    for i, d in enumerate(pd.bdate_range("2024-01-02", periods=10).strftime("%Y-%m-%d")):
        # a climbs hard in last 5
        pa = 10.0 * (1.04**i) if i >= 5 else 10.0
        if i < 5:
            pa = 10.0
        else:
            pa = 10.0 * (1.2 ** ((i - 4) / 5))  # end ~ +20% over 5 steps from i=5..9
        # simpler: fixed closes
        rows.append({"code": "a", "date": d, "close": [10, 10, 10, 10, 10, 10, 11, 12, 13, 14][i]})
        rows.append({"code": "b", "date": d, "close": [10, 10, 10, 10, 10, 10, 10.2, 10.4, 10.5, 10.6][i]})
        rows.append({"code": "c", "date": d, "close": 10.0})
    return pd.DataFrame(rows)


def test_overheat_codes_flags_hot_only():
    daily = _daily_hot_and_cold()
    as_of = daily["date"].max()
    hot = overheat_codes(daily, as_of, ["a", "b", "c"], lookback=5, max_ret=0.15)
    assert "a" in hot
    assert "b" not in hot
    assert "c" not in hot


def test_filter_new_entries_keeps_held_hot():
    alpha = {"a": 1.0, "b": 0.9, "c": 0.8}
    out = filter_new_entries(alpha, current={"a": 0.1}, hot={"a", "b"})
    assert "a" in out  # 已持仓保留
    assert "b" not in out  # 未持仓过热剔除
    assert "c" in out
