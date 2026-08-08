"""validate_library：实际起止与市场级缺日。"""

from __future__ import annotations

import unittest

import pandas as pd

from scripts.data.validate_library import data_span_and_market_gaps, _fmt_dates


class DataSpanAndMarketGapsTests(unittest.TestCase):
    def test_reports_span_and_middle_gaps(self):
        daily = pd.DataFrame(
            {
                "code": ["000001", "000001", "000002"],
                "date": ["2024-01-02", "2024-01-04", "2024-01-02"],
            }
        )
        cal = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
        lo, hi, span, missing, after = data_span_and_market_gaps(
            daily, cal, "2024-01-01", "2024-01-10"
        )
        self.assertEqual(lo, "2024-01-02")
        self.assertEqual(hi, "2024-01-04")
        self.assertEqual(span, ["2024-01-02", "2024-01-03", "2024-01-04"])
        self.assertEqual(missing, ["2024-01-03"])  # 中间缺；非交易日已不在 cal
        self.assertEqual(after, ["2024-01-05"])  # 末日之后未入库

    def test_no_gap_when_every_trading_day_has_bar(self):
        daily = pd.DataFrame(
            {
                "code": ["000001", "000001"],
                "date": ["2024-01-02", "2024-01-03"],
            }
        )
        cal = ["2024-01-02", "2024-01-03"]
        lo, hi, span, missing, after = data_span_and_market_gaps(
            daily, cal, "2024-01-01", "2024-01-03"
        )
        self.assertEqual((lo, hi), ("2024-01-02", "2024-01-03"))
        self.assertEqual(span, cal)
        self.assertEqual(missing, [])
        self.assertEqual(after, [])

    def test_window_clips_span(self):
        daily = pd.DataFrame(
            {
                "code": ["000001", "000001", "000001"],
                "date": ["2023-12-29", "2024-01-02", "2024-06-01"],
            }
        )
        cal = ["2023-12-29", "2024-01-02", "2024-01-03", "2024-06-01"]
        lo, hi, span, missing, after = data_span_and_market_gaps(
            daily, cal, "2024-01-01", "2024-01-31"
        )
        self.assertEqual((lo, hi), ("2024-01-02", "2024-01-02"))
        self.assertEqual(span, ["2024-01-02"])
        self.assertEqual(missing, [])
        self.assertEqual(after, ["2024-01-03"])

    def test_fmt_dates_wraps(self):
        dates = [f"2024-01-{i:02d}" for i in range(1, 13)]
        text = _fmt_dates(dates, per_line=5)
        self.assertIn("\n", text)
        self.assertIn("2024-01-01", text)
        self.assertIn("2024-01-12", text)


if __name__ == "__main__":
    unittest.main()
