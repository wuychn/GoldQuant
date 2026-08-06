"""backfill_daily_meta：默认只拉仍缺市值的码。"""

from __future__ import annotations

import unittest

import pandas as pd

from scripts.data.backfill_daily_meta import codes_needing_mv_fetch


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


if __name__ == "__main__":
    unittest.main()
