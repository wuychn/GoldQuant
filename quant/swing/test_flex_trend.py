"""flex_trend 评分单测。"""

from __future__ import annotations

import numpy as np

from quant.swing.flex_trend import FlexTrendParams, score_row


def _uptrend(n=120, start=10.0):
    close = start * np.cumprod(1.0 + np.full(n, 0.008))
    high = close * 1.01
    amount = np.full(n, 5e8)
    return close, high, amount


def test_score_accepts_clean_uptrend():
    c, h, a = _uptrend()
    s = score_row(c, h, a, "优质公司", FlexTrendParams())
    assert s is not None and s > 0


def test_score_rejects_st():
    c, h, a = _uptrend()
    assert score_row(c, h, a, "ST垃圾", FlexTrendParams()) is None


def test_score_rejects_deep_pullback():
    c, h, a = _uptrend()
    # 暴跌远离高点
    c = c.copy()
    c[-5:] = c[-6] * 0.7
    h = np.maximum.accumulate(c) * 1.01
    assert score_row(c, h, a, "正常", FlexTrendParams(max_pullback_from_high=0.10)) is None
