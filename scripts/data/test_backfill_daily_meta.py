"""backfill_daily_meta：默认只拉仍缺市值的码；边拉边落盘后可续传。"""

from __future__ import annotations

import unittest

import pandas as pd

from scripts.data.backfill_daily_meta import codes_needing_mv_fetch, merge_mv_into_daily


class CodesNeedingMvFetchTests(unittest.TestCase):
    def test_skips_codes_fully_filled(self):
        daily = pd.DataFrame(
            {
                "code": ["000001", "000001", "000002", "000002"],
                "float_mv": [1.0, 2.0, None, 3.0],
                "total_mv": [10.0, 20.0, 30.0, None],
            }
        )
        # 000001 全齐；000002 两列都有缺 → 只拉 000002
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


class MergeMvResumeTests(unittest.TestCase):
    def test_merge_then_skip_on_resume(self):
        """落盘等价：merge 后该码不再进入待拉列表（断点续传前提）。"""
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
        # 已补齐的 000001 不再待拉；000002 仍缺
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
