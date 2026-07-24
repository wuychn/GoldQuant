"""机构因子单元测试。"""

from __future__ import annotations

import unittest

from quant.factors.sector import compute_sector_factors
from quant.sector.engine import build_sector_rows, eligible_sector_names


class TestSectorFactors(unittest.TestCase):
    def test_climax_with_strong_fund_still_eligible(self) -> None:
        row = {
            "行业": "AI应用",
            "行业-涨跌幅": 6.0,
            "净额": 25,
            "上涨家数": 18,
            "下跌家数": 12,
        }
        fac = compute_sector_factors(
            name="AI应用",
            gain_row=row,
            rank_gain=2,
            rank_fund=1,
            persistence_days=4,
            index_chg=0.5,
        )
        self.assertGreaterEqual(fac.fund_score, 68)
        self.assertTrue(fac.eligible, fac.note)

    def test_climax_crowded_low_breadth_blocked(self) -> None:
        row = {
            "行业": "短线题材",
            "行业-涨跌幅": 6.5,
            "净额": 1,
            "上涨家数": 5,
            "下跌家数": 45,
        }
        fac = compute_sector_factors(
            name="短线题材",
            gain_row=row,
            rank_gain=1,
            rank_fund=8,
            persistence_days=1,
            index_chg=0.2,
        )
        self.assertLess(fac.breadth_score, 45)
        self.assertFalse(fac.eligible)

    def test_industry_breadth_from_up_down(self) -> None:
        payload = {
            "概念板块": {"涨幅榜": [], "资金流入榜": []},
            "行业板块": {
                "涨幅榜": [
                    {
                        "行业": "半导体",
                        "行业-涨跌幅": 2.5,
                        "净额": 5,
                        "上涨家数": 80,
                        "下跌家数": 20,
                    }
                ]
            },
            "赚钱效应": {"上涨": 2000, "下跌": 1500, "涨停": 50},
            "大盘指数": [{"代码": "000001", "涨跌幅": 0.3}],
        }
        rows = build_sector_rows(payload, use_ak_breadth=False)
        semi = next(r for r in rows if r.name == "半导体")
        self.assertGreaterEqual(semi.breadth_score, 75)
        self.assertIn("半导体", eligible_sector_names(rows))


if __name__ == "__main__":
    unittest.main()
