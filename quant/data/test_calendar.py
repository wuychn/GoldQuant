"""交易日历：已收盘日（开机补跑）。"""

from __future__ import annotations

import unittest
from datetime import date, datetime
from unittest.mock import patch

from quant.data.calendar import closed_session_as_of


class ClosedSessionAsOfTests(unittest.TestCase):
    def test_before_16_uses_prev_trading_day(self):
        now = datetime(2026, 10, 9, 15, 59, 0)
        with (
            patch("quant.data.calendar.is_trading_day", return_value=True),
            patch("quant.data.calendar.prev_trading_day", return_value=date(2026, 10, 8)) as prev,
        ):
            self.assertEqual(closed_session_as_of(now), date(2026, 10, 8))
            prev.assert_called_once_with(date(2026, 10, 9))

    def test_at_16_on_trading_day_uses_today(self):
        now = datetime(2026, 10, 9, 16, 0, 0)
        with (
            patch("quant.data.calendar.is_trading_day", return_value=True),
            patch("quant.data.calendar.prev_trading_day") as prev,
        ):
            self.assertEqual(closed_session_as_of(now), date(2026, 10, 9))
            prev.assert_not_called()

    def test_lhb_before_19_uses_prev(self):
        now = datetime(2026, 10, 9, 18, 30, 0)
        with (
            patch("quant.data.calendar.is_trading_day", return_value=True),
            patch("quant.data.calendar.prev_trading_day", return_value=date(2026, 10, 8)) as prev,
        ):
            self.assertEqual(closed_session_as_of(now, close_hour=19), date(2026, 10, 8))
            prev.assert_called_once_with(date(2026, 10, 9))

    def test_lhb_at_19_uses_today(self):
        now = datetime(2026, 10, 9, 19, 0, 0)
        with (
            patch("quant.data.calendar.is_trading_day", return_value=True),
            patch("quant.data.calendar.prev_trading_day") as prev,
        ):
            self.assertEqual(closed_session_as_of(now, close_hour=19), date(2026, 10, 9))
            prev.assert_not_called()

    def test_weekend_even_after_19_uses_prev(self):
        now = datetime(2026, 10, 10, 20, 0, 0)
        with (
            patch("quant.data.calendar.is_trading_day", return_value=False),
            patch("quant.data.calendar.prev_trading_day", return_value=date(2026, 10, 9)),
        ):
            self.assertEqual(closed_session_as_of(now, close_hour=19), date(2026, 10, 9))
