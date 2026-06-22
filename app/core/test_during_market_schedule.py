"""盘中智能盯盘默认定时测试。"""

from __future__ import annotations

import unittest

from app.core.config import build_during_market_schedule


class DuringMarketScheduleTests(unittest.TestCase):
    def test_default_seven_minute_sessions(self) -> None:
        times = build_during_market_schedule().split(",")
        self.assertEqual(times[0], "09:30")
        self.assertEqual(times[17], "11:29")
        self.assertEqual(times[18], "13:00")
        self.assertEqual(times[-1], "14:59")
        self.assertEqual(len(times), 36)
        self.assertNotIn("11:30", times)
        self.assertNotIn("12:00", times)


if __name__ == "__main__":
    unittest.main()
