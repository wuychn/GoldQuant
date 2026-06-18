"""当日 K 线形态评分测试。"""

from __future__ import annotations

import unittest

from quant.scoring.dimensions.day_bar_shape import score_day_bar_shape


class DayBarShapeTests(unittest.TestCase):
    def test_bearish_candle_penalty(self) -> None:
        stock = {
            "盘口": {"开盘": 10.0, "最新": 9.5, "最高": 10.2},
        }
        s, d = score_day_bar_shape(stock)
        self.assertTrue(d.get("收阴"))
        self.assertLess(s, 85)
        self.assertGreater(d.get("阴线扣分", 0), 0)

    def test_spike_fade_penalty(self) -> None:
        stock = {
            "盘口": {"开盘": 10.0, "最新": 10.3, "最高": 10.8},
        }
        s, d = score_day_bar_shape(stock)
        self.assertTrue(d.get("冲高回落"))
        self.assertLess(s, 85)

    def test_clean_day_high_score(self) -> None:
        stock = {
            "盘口": {"开盘": 10.0, "最新": 10.5, "最高": 10.55},
        }
        s, d = score_day_bar_shape(stock)
        self.assertFalse(d.get("收阴"))
        self.assertFalse(d.get("冲高回落"))
        self.assertGreaterEqual(s, 81)


if __name__ == "__main__":
    unittest.main()
