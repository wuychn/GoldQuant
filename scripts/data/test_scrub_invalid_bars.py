"""scrub_invalid_bars：脏行掩码。"""

from __future__ import annotations

import unittest

import pandas as pd

from scripts.data.scrub_invalid_bars import invalid_bar_mask


class InvalidBarMaskTests(unittest.TestCase):
    def test_flags_zero_and_inconsistent(self):
        daily = pd.DataFrame(
            {
                "code": ["a", "b", "c"],
                "date": ["2026-08-04"] * 3,
                "open": [10.0, 0.0, 0.0],
                "high": [11.0, 0.0, 0.0],
                "low": [9.0, 0.0, 0.0],
                "close": [10.5, 0.0, 22.0],
                "volume": [1.0, 0.0, 0.0],
                "amount": [1.0, 0.0, 0.0],
            }
        )
        bad = invalid_bar_mask(daily)
        self.assertEqual(bad.tolist(), [False, True, True])


if __name__ == "__main__":
    unittest.main()
