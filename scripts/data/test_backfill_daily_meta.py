"""backfill_daily_meta：默认只拉仍缺市值的码；边拉边落盘后可续传。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from scripts.data.backfill_daily_meta import (
    add_no_mv_dates,
    codes_needing_mv_fetch,
    is_value_em_unavailable,
    load_no_mv_map,
    merge_mv_into_daily,
    remaining_mv_miss_dates,
)


class CodesNeedingMvFetchTests(unittest.TestCase):
    def test_skips_codes_fully_filled(self):
        daily = pd.DataFrame(
            {
                "code": ["000001", "000001", "000002", "000002"],
                "float_mv": [1.0, 2.0, None, 3.0],
                "total_mv": [10.0, 20.0, 30.0, None],
            }
        )
        got = codes_needing_mv_fetch(daily, ["000001", "000002"], force=False)
        self.assertEqual(got, ["000002"])

    def test_force_fetches_all(self):
        daily = pd.DataFrame(
            {
                "code": ["000001", "000002"],
                "float_mv": [1.0, 2.0],
                "total_mv": [10.0, 20.0],
            }
        )
        got = codes_needing_mv_fetch(daily, ["000001", "000002"], force=True)
        self.assertEqual(got, ["000001", "000002"])

    def test_missing_column_treated_as_need(self):
        daily = pd.DataFrame({"code": ["000001", "000002"], "close": [1.0, 2.0]})
        got = codes_needing_mv_fetch(daily, ["000001", "000002"], force=False)
        self.assertEqual(got, ["000001", "000002"])

    def test_skips_unavailable_codes(self):
        daily = pd.DataFrame(
            {
                "code": ["000001", "600068"],
                "float_mv": [None, None],
                "total_mv": [None, None],
            }
        )
        got = codes_needing_mv_fetch(
            daily, ["000001", "600068"], force=False, unavailable={"600068"}
        )
        self.assertEqual(got, ["000001"])

    def test_force_ignores_unavailable(self):
        daily = pd.DataFrame(
            {
                "code": ["600068"],
                "float_mv": [None],
                "total_mv": [None],
            }
        )
        got = codes_needing_mv_fetch(
            daily, ["600068"], force=True, unavailable={"600068"}
        )
        self.assertEqual(got, ["600068"])

    def test_skips_when_only_no_mv_dates_remain(self):
        """成功拉过后仍缺的日已豁免 → 不再重拉。"""
        daily = pd.DataFrame(
            {
                "code": ["000001", "000001", "000002"],
                "date": ["2024-01-02", "2024-01-03", "2024-01-02"],
                "float_mv": [100.0, None, None],
                "total_mv": [200.0, None, None],
            }
        )
        got = codes_needing_mv_fetch(
            daily,
            ["000001", "000002"],
            force=False,
            no_mv_map={"000001": {"2024-01-03"}},
        )
        self.assertEqual(got, ["000002"])

    def test_still_fetches_unexempted_miss_dates(self):
        daily = pd.DataFrame(
            {
                "code": ["000001", "000001"],
                "date": ["2024-01-02", "2024-01-03"],
                "float_mv": [None, None],
                "total_mv": [None, None],
            }
        )
        got = codes_needing_mv_fetch(
            daily,
            ["000001"],
            force=False,
            no_mv_map={"000001": {"2024-01-02"}},
        )
        self.assertEqual(got, ["000001"])

    def test_force_ignores_no_mv_map(self):
        daily = pd.DataFrame(
            {
                "code": ["000001"],
                "date": ["2024-01-02"],
                "float_mv": [None],
                "total_mv": [None],
            }
        )
        got = codes_needing_mv_fetch(
            daily,
            ["000001"],
            force=True,
            no_mv_map={"000001": {"2024-01-02"}},
        )
        self.assertEqual(got, ["000001"])


class NoMvDatesTests(unittest.TestCase):
    def test_add_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            self.assertEqual(add_no_mv_dates(home, "000001", {"2024-01-02", "2024-01-03"}), 2)
            self.assertEqual(add_no_mv_dates(home, "000001", {"2024-01-03", "2024-01-04"}), 1)
            mp = load_no_mv_map(home)
            self.assertEqual(mp["000001"], {"2024-01-02", "2024-01-03", "2024-01-04"})

    def test_remaining_after_partial_merge(self):
        daily = pd.DataFrame(
            {
                "code": ["000001", "000001"],
                "date": ["2024-01-02", "2024-01-03"],
                "float_mv": [None, None],
                "total_mv": [None, None],
            }
        )
        daily2 = merge_mv_into_daily(
            daily, {"000001": {"2024-01-02": (100.0, 200.0)}}
        )
        self.assertEqual(remaining_mv_miss_dates(daily2, "000001"), {"2024-01-03"})


class UnavailableClassifyTests(unittest.TestCase):
    def test_akshare_none_result(self):
        self.assertTrue(
            is_value_em_unavailable("TypeError: 'NoneType' object is not subscriptable")
        )

    def test_friendly_message(self):
        self.assertTrue(is_value_em_unavailable("源无市值数据（退市或接口无返回）"))

    def test_real_error_not_unavailable(self):
        self.assertFalse(is_value_em_unavailable("HTTPError: 503"))


class MergeMvResumeTests(unittest.TestCase):
    def test_merge_then_skip_on_resume(self):
        daily = pd.DataFrame(
            {
                "code": ["000001", "000001", "000002"],
                "date": ["2024-01-02", "2024-01-03", "2024-01-02"],
                "close": [10.0, 11.0, 20.0],
                "float_mv": [None, None, None],
                "total_mv": [None, None, None],
            }
        )
        mv = {
            "000001": {
                "2024-01-02": (100.0, 200.0),
                "2024-01-03": (110.0, 210.0),
            }
        }
        daily2 = merge_mv_into_daily(daily, mv)
        self.assertEqual(float(daily2.loc[daily2["date"] == "2024-01-02", "float_mv"].iloc[0]), 100.0)
        got = codes_needing_mv_fetch(daily2, ["000001", "000002"], force=False)
        self.assertEqual(got, ["000002"])

    def test_merge_does_not_overwrite_existing(self):
        daily = pd.DataFrame(
            {
                "code": ["000001"],
                "date": ["2024-01-02"],
                "float_mv": [99.0],
                "total_mv": [199.0],
            }
        )
        mv = {"000001": {"2024-01-02": (1.0, 2.0)}}
        daily2 = merge_mv_into_daily(daily, mv)
        self.assertEqual(float(daily2["float_mv"].iloc[0]), 99.0)


if __name__ == "__main__":
    unittest.main()
