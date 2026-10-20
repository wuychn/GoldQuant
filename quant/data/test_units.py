"""成交量股/手对齐与换手推导。"""

from __future__ import annotations

import unittest

import pandas as pd

from quant.data.units import (
    align_volume_to_shares,
    fill_pre_close,
    fill_turnover_from_mv,
    last_close_by_code,
    lots_volume_mask,
)


class AlignVolumeTests(unittest.TestCase):
    def test_lots_multiplied_to_shares(self):
        # 手：vol=10000, close=10, amount=10e6 → ratio=100
        df = pd.DataFrame(
            {"close": [10.0], "volume": [10_000.0], "amount": [10_000_000.0]}
        )
        out = align_volume_to_shares(df)
        self.assertTrue(bool(lots_volume_mask(df).iloc[0]))
        self.assertAlmostEqual(float(out["volume"].iloc[0]), 1_000_000.0)

    def test_already_shares_unchanged(self):
        df = pd.DataFrame(
            {"close": [10.0], "volume": [1_000_000.0], "amount": [10_000_000.0]}
        )
        out = align_volume_to_shares(df)
        self.assertFalse(bool(lots_volume_mask(df).iloc[0]))
        self.assertAlmostEqual(float(out["volume"].iloc[0]), 1_000_000.0)

    def test_idempotent(self):
        df = pd.DataFrame(
            {"close": [10.0], "volume": [10_000.0], "amount": [10_000_000.0]}
        )
        once = align_volume_to_shares(df)
        twice = align_volume_to_shares(once)
        self.assertAlmostEqual(float(once["volume"].iloc[0]), float(twice["volume"].iloc[0]))

    def test_zero_volume_skipped(self):
        df = pd.DataFrame({"close": [10.0], "volume": [0.0], "amount": [0.0]})
        out = align_volume_to_shares(df)
        self.assertEqual(float(out["volume"].iloc[0]), 0.0)


class FillPreCloseTests(unittest.TestCase):
    def test_shifts_prior_close(self):
        df = pd.DataFrame(
            {
                "code": ["000001", "000001"],
                "date": ["2020-01-02", "2020-01-03"],
                "close": [10.0, 11.0],
                "pre_close": [pd.NA, pd.NA],
            }
        )
        out = fill_pre_close(df)
        self.assertTrue(pd.isna(out["pre_close"].iloc[0]))
        self.assertAlmostEqual(float(out["pre_close"].iloc[1]), 10.0)

    def test_seed_fills_first_row(self):
        df = pd.DataFrame(
            {
                "code": ["000001"],
                "date": ["2021-01-04"],
                "close": [12.0],
                "pre_close": [pd.NA],
            }
        )
        out = fill_pre_close(df, seed={"000001": 11.5})
        self.assertAlmostEqual(float(out["pre_close"].iloc[0]), 11.5)

    def test_does_not_overwrite(self):
        df = pd.DataFrame(
            {
                "code": ["000001", "000001"],
                "date": ["2020-01-02", "2020-01-03"],
                "close": [10.0, 11.0],
                "pre_close": [9.9, 10.1],
            }
        )
        out = fill_pre_close(df)
        self.assertAlmostEqual(float(out["pre_close"].iloc[1]), 10.1)

    def test_last_close_by_code(self):
        df = pd.DataFrame(
            {
                "code": ["000001", "000001", "000002"],
                "date": ["2020-12-30", "2020-12-31", "2020-12-31"],
                "close": [10.0, 11.0, 20.0],
            }
        )
        self.assertEqual(last_close_by_code(df), {"000001": 11.0, "000002": 20.0})


class FillTurnoverTests(unittest.TestCase):
    def test_fills_nan_from_amount_over_mv(self):
        df = pd.DataFrame(
            {
                "amount": [2e9],
                "float_mv": [4e11],
                "turnover_rate": [pd.NA],
            }
        )
        out = fill_turnover_from_mv(df)
        self.assertAlmostEqual(float(out["turnover_rate"].iloc[0]), 0.5)

    def test_does_not_overwrite(self):
        df = pd.DataFrame(
            {"amount": [2e9], "float_mv": [4e11], "turnover_rate": [1.2]}
        )
        out = fill_turnover_from_mv(df)
        self.assertAlmostEqual(float(out["turnover_rate"].iloc[0]), 1.2)


if __name__ == "__main__":
    unittest.main()
