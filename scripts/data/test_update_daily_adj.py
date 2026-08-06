"""update_daily 除权检测：须用源站原始昨收，再覆盖写库。"""

from __future__ import annotations

import unittest

import pandas as pd

from scripts.data.update_daily import detect_ex_and_align_pre_close


class DetectExAlignTests(unittest.TestCase):
    def test_detect_ex_before_align_pre_close(self):
        """源站昨收与库内 T-1 不一致 → 检出除权；随后 spot.pre_close 对齐到库内。"""
        spot = pd.DataFrame({
            "code": ["000001", "000002"],
            "pre_close": [12.0, 5.0],  # 000001 源站昨收 12，库内 10 → 除权
            "close": [11.5, 5.1],
        })
        prev_map = {"000001": 10.0, "000002": 5.0}

        ex = detect_ex_and_align_pre_close(spot, prev_map)

        self.assertEqual(ex, ["000001"])
        self.assertEqual(float(spot.loc[spot["code"] == "000001", "pre_close"].iloc[0]), 10.0)
        self.assertEqual(float(spot.loc[spot["code"] == "000002", "pre_close"].iloc[0]), 5.0)

    def test_align_without_ex_keeps_empty(self):
        """无差异 → 无除权，仍对齐 pre_close。"""
        spot = pd.DataFrame({"code": ["000001"], "pre_close": [10.0]})
        prev_map = {"000001": 10.0}

        ex = detect_ex_and_align_pre_close(spot, prev_map)

        self.assertEqual(ex, [])
        self.assertEqual(float(spot["pre_close"].iloc[0]), 10.0)


if __name__ == "__main__":
    unittest.main()
