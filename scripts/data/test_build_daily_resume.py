"""build_daily 智能断点：incomplete_codes 完整性检查 / market_missing_dates 离线单测。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from scripts.data.build_daily import (
    incomplete_codes,
    last_cal_day_on_or_before,
    market_missing_dates,
)


CAL = [
    "2026-07-20",
    "2026-07-21",
    "2026-07-22",
    "2026-07-23",
    "2026-07-24",
]


def _daily(rows: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["code", "date"])


class IncompleteCodesTests(unittest.TestCase):
    def test_last_cal_day_on_or_before(self):
        self.assertEqual(last_cal_day_on_or_before(CAL, "2026-07-23"), "2026-07-23")
        self.assertEqual(last_cal_day_on_or_before(CAL, "2026-07-25"), "2026-07-24")
        self.assertIsNone(last_cal_day_on_or_before([], "2026-07-24"))

    def test_empty_store_returns_all(self):
        codes = ["000001", "000002"]
        got = incomplete_codes(
            codes,
            start="2026-07-20",
            end="2026-07-24",
            daily=pd.DataFrame(),
            calendar=CAL,
        )
        self.assertEqual(got, codes)

    def test_only_missing_code(self):
        rows = [("000001", d) for d in CAL]
        got = incomplete_codes(
            ["000001", "000002"],
            start="2026-07-20",
            end="2026-07-24",
            daily=_daily(rows),
            calendar=CAL,
        )
        self.assertEqual(got, ["000002"])

    def test_missing_single_middle_day(self):
        """中间缺一天 → 待拉。"""
        rows = [("000001", d) for d in CAL if d != "2026-07-22"]
        got = incomplete_codes(
            ["000001"],
            start="2026-07-20",
            end="2026-07-24",
            daily=_daily(rows),
            calendar=CAL,
        )
        self.assertEqual(got, ["000001"])

    def test_missing_tail_days(self):
        rows = [("000001", d) for d in CAL[:-2]]
        got = incomplete_codes(
            ["000001"],
            start="2026-07-20",
            end="2026-07-24",
            daily=_daily(rows),
            calendar=CAL,
        )
        self.assertEqual(got, ["000001"])

    def test_missing_head_days(self):
        rows = [("000001", d) for d in CAL[2:]]
        got = incomplete_codes(
            ["000001"],
            start="2026-07-20",
            end="2026-07-24",
            daily=_daily(rows),
            calendar=CAL,
        )
        self.assertEqual(got, ["000001"])

    def test_listing_map_skips_pre_ipo_days(self):
        """有上市日时，上市前交易日不计入缺数。"""
        rows = [("000001", d) for d in CAL if d >= "2026-07-22"]
        got = incomplete_codes(
            ["000001"],
            start="2026-07-20",
            end="2026-07-24",
            daily=_daily(rows),
            calendar=CAL,
            listing_map={"000001": "2026-07-22"},
        )
        self.assertEqual(got, [])

    def test_complete_code_skipped(self):
        rows = [("000001", d) for d in CAL]
        got = incomplete_codes(
            ["000001"],
            start="2026-07-20",
            end="2026-07-24",
            daily=_daily(rows),
            calendar=CAL,
        )
        self.assertEqual(got, [])

    def test_preserves_order(self):
        rows = [("a", d) for d in CAL]
        got = incomplete_codes(
            ["b", "a", "c"],
            start="2026-07-20",
            end="2026-07-24",
            daily=_daily(rows),
            calendar=CAL,
        )
        self.assertEqual(got, ["b", "c"])

    def test_fetch_plan_tail_only(self):
        """尾部缺两天 → 只补那两天窗口，不重拉全历史。"""
        from scripts.data.build_daily import incomplete_fetch_plans

        rows = [("000001", d) for d in CAL[:-2]]
        plans = incomplete_fetch_plans(
            ["000001"],
            start="2026-07-20",
            end="2026-07-24",
            daily=_daily(rows),
            calendar=CAL,
        )
        self.assertEqual(plans, [("000001", "2026-07-23", "2026-07-24")])

    def test_fetch_plan_single_middle_day(self):
        from scripts.data.build_daily import incomplete_fetch_plans

        rows = [("000001", d) for d in CAL if d != "2026-07-22"]
        plans = incomplete_fetch_plans(
            ["000001"],
            start="2026-07-20",
            end="2026-07-24",
            daily=_daily(rows),
            calendar=CAL,
        )
        self.assertEqual(plans, [("000001", "2026-07-22", "2026-07-22")])

    def test_fetch_plan_empty_code_full_window(self):
        from scripts.data.build_daily import incomplete_fetch_plans

        plans = incomplete_fetch_plans(
            ["000002"],
            start="2026-07-20",
            end="2026-07-24",
            daily=_daily([("000001", d) for d in CAL]),
            calendar=CAL,
        )
        self.assertEqual(plans, [("000002", "2026-07-20", "2026-07-24")])


class MarketMissingDatesTests(unittest.TestCase):
    @patch("scripts.data.maintain.scan_missing_dates")
    def test_filters_start(self, mock_scan):
        mock_scan.return_value = ["2026-07-20", "2026-07-22", "2026-07-24"]
        miss = market_missing_dates(start="2026-07-22", end="2026-07-24")
        self.assertEqual(miss, ["2026-07-22", "2026-07-24"])
        mock_scan.assert_called_once_with("2026-07-24")

    @patch("scripts.data.maintain.scan_missing_dates")
    def test_gap_fill_needed_when_missing(self, mock_scan):
        mock_scan.return_value = ["2026-07-22"]
        self.assertEqual(
            market_missing_dates(start="2026-07-20", end="2026-07-24"),
            ["2026-07-22"],
        )

    @patch("scripts.data.maintain.scan_missing_dates")
    def test_gap_fill_skip_when_complete(self, mock_scan):
        mock_scan.return_value = []
        self.assertEqual(market_missing_dates(start="2026-07-20", end="2026-07-24"), [])


if __name__ == "__main__":
    unittest.main()
