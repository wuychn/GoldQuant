"""连续竞价分钟累计测试。"""

from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from quant.timeutil import trading_minutes_between

_TZ = ZoneInfo("Asia/Shanghai")


def _ts(h: int, m: int) -> datetime:
    return datetime(2026, 6, 16, h, m, 0, tzinfo=_TZ)


class TradingMinutesTests(unittest.TestCase):
    def test_same_morning_segment(self) -> None:
        self.assertAlmostEqual(trading_minutes_between(_ts(9, 37), _ts(9, 47)), 10.0)

    def test_excludes_lunch_break(self) -> None:
        mins = trading_minutes_between(_ts(11, 20), _ts(13, 10))
        self.assertAlmostEqual(mins, 20.0)

    def test_afternoon_only(self) -> None:
        self.assertAlmostEqual(trading_minutes_between(_ts(13, 0), _ts(13, 20)), 20.0)


if __name__ == "__main__":
    unittest.main()
