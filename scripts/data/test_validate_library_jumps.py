"""validate_library 后复权跳空：豁免停牌缺口与原料同步大波动。"""

from __future__ import annotations

import unittest

import pandas as pd

from scripts.data.validate_library import adj_jump_suspects


class AdjJumpSuspectsTests(unittest.TestCase):
    def _adj_ones(self, daily: pd.DataFrame) -> pd.DataFrame:
        return daily[["code", "date"]].assign(hfq_factor=1.0)

    def test_skips_non_adjacent_calendar_gap(self):
        """长停牌复牌：库内相邻但日历不相邻 → 不记嫌疑。"""
        daily = pd.DataFrame(
            {
                "code": ["000792", "000792"],
                "date": ["2021-06-30", "2021-08-10"],
                "close": [8.84, 35.90],
            }
        )
        cal = ["2021-06-30", "2021-07-01", "2021-08-10"]  # 中间有交易日 → 不相邻
        bad = adj_jump_suspects(daily, self._adj_ones(daily), cal)
        self.assertTrue(bad.empty)

    def test_skips_when_raw_also_jumps(self):
        """次新大波动：原料与后复权同步大跳 → 豁免。"""
        daily = pd.DataFrame(
            {
                "code": ["001373", "001373"],
                "date": ["2023-06-01", "2023-06-02"],
                "close": [46.05, 61.94],
            }
        )
        cal = ["2023-06-01", "2023-06-02"]
        bad = adj_jump_suspects(daily, self._adj_ones(daily), cal)
        self.assertTrue(bad.empty)

    def test_flags_hfq_spike_when_raw_calm(self):
        """复权错接：原料平稳、后复权尖刺 → 嫌疑。"""
        daily = pd.DataFrame(
            {
                "code": ["000001", "000001"],
                "date": ["2024-01-02", "2024-01-03"],
                "close": [10.0, 10.1],
            }
        )
        adj = pd.DataFrame(
            {
                "code": ["000001", "000001"],
                "date": ["2024-01-02", "2024-01-03"],
                "hfq_factor": [1.0, 2.0],  # 因子突变 → hfq close 约翻倍
            }
        )
        cal = ["2024-01-02", "2024-01-03"]
        bad = adj_jump_suspects(daily, adj, cal)
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad.iloc[0]["code"], "000001")

    def test_skips_zero_close_rows(self):
        daily = pd.DataFrame(
            {
                "code": ["000838", "000838"],
                "date": ["2026-08-03", "2026-08-04"],
                "close": [2.5, 0.0],
            }
        )
        cal = ["2026-08-03", "2026-08-04"]
        bad = adj_jump_suspects(daily, self._adj_ones(daily), cal)
        self.assertTrue(bad.empty)


if __name__ == "__main__":
    unittest.main()
