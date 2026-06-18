"""个股资金流评分测试。"""

from __future__ import annotations

import unittest

from quant.scoring.dimensions.stock_fund_flow import score_stock_fund_flow


class StockFundFlowScoreTests(unittest.TestCase):
    def test_inflow_scores_high(self) -> None:
        stock = {
            "流通市值": 10_000_000_000.0,
            "个股资金流": {"净额": "500 万元"},
        }
        s, d = score_stock_fund_flow(stock)
        self.assertGreaterEqual(s, 80)
        self.assertGreater(d["净额"], 0)

    def test_any_outflow_is_negative(self) -> None:
        stock = {
            "流通市值": 100_000_000_000.0,
            "个股资金流": {"净额": "-1 万元"},
        }
        s, d = score_stock_fund_flow(stock)
        self.assertLess(s, 0)
        self.assertGreater(d["流出基础惩罚"], 0)

    def test_outflow_penalized_by_float_cap(self) -> None:
        small = {
            "流通市值": 10_000_000_000.0,
            "个股资金流": {"净额": "-200 万元"},
        }
        large = {
            "流通市值": 2_000_000_000.0,
            "个股资金流": {"净额": "-200 万元"},
        }
        s_small, _ = score_stock_fund_flow(small)
        s_large, d_large = score_stock_fund_flow(large)
        self.assertLess(s_small, 0)
        self.assertLess(s_large, 0)
        self.assertLess(s_large, s_small)
        self.assertGreater(d_large["流出占流通市值"], 0.005)
        self.assertGreater(d_large["流出占比惩罚"], 0)

    def test_consecutive_outflow_extra_penalty(self) -> None:
        base = {
            "流通市值": 5_000_000_000.0,
            "个股资金流": {"净额": "-500 万元"},
        }
        s1, _ = score_stock_fund_flow(base)
        streak = {
            **base,
            "个股资金流日线": [
                {"主力净流入-净额": -1e8},
                {"主力净流入-净额": -2e8},
                {"主力净流入-净额": -3e8},
            ],
        }
        s3, d = score_stock_fund_flow(streak)
        self.assertGreaterEqual(d["连续流出天数"], 3)
        self.assertGreater(d["连续流出惩罚"], 0)
        self.assertLess(s3, s1)
        self.assertLess(s3, -40)


if __name__ == "__main__":
    unittest.main()
