"""is_trend_choppy 边界测试。"""

from __future__ import annotations

import unittest

from quant.strategy.main_wave import is_trend_choppy, ma_bull_stack


def _hist(closes: list[float]) -> list[dict]:
    rows = []
    prev = closes[0]
    for i, c in enumerate(closes):
        chg = (c - prev) / prev * 100 if prev else 0
        rows.append({"收盘": c, "涨跌幅": chg})
        prev = c
    return rows


def _stock(closes: list[float], *, ma5: float, ma10: float, ma20: float) -> dict:
    return {
        "历史行情": _hist(closes),
        "盘口": {"最新": closes[-1]},
        "技术指标": {"MA5": ma5, "MA10": ma10, "MA20": ma20, "latest_close": closes[-1]},
    }


class TrendChoppyTests(unittest.TestCase):
    def test_low_60d_net_but_recent_breakout_not_choppy(self) -> None:
        """60 日 net 低，但均线多头 + 近 20 日涨幅达标 → 不判 choppy。"""
        base = [10.0] * 45
        tail = [10.0 + i * 0.15 for i in range(1, 16)]
        closes = base + tail
        last = closes[-1]
        stock = _stock(closes, ma5=last * 0.99, ma10=last * 0.97, ma20=last * 0.94)
        cfg = {"choppy_lookback_days": 60, "choppy_min_net_pct": 5.0, "choppy_recent_days": 20}
        self.assertTrue(ma_bull_stack({"ma5": stock["技术指标"]["MA5"], "ma10": stock["技术指标"]["MA10"], "ma20": stock["技术指标"]["MA20"], "last": last}))
        self.assertFalse(is_trend_choppy(stock, cfg))

    def test_low_net_without_breakout_is_choppy(self) -> None:
        closes = [10.0 + (0.02 if i % 2 == 0 else -0.02) for i in range(65)]
        last = closes[-1]
        stock = _stock(closes, ma5=10.0, ma10=10.1, ma20=10.2, )
        cfg = {"choppy_lookback_days": 60, "choppy_min_net_pct": 5.0}
        self.assertTrue(is_trend_choppy(stock, cfg))


if __name__ == "__main__":
    unittest.main()
