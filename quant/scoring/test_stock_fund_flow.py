"""个股资金流评分测试。"""

from __future__ import annotations

import unittest

from quant.scoring.dimensions.stock_fund_flow import score_stock_fund_flow


class StockFundFlowScoreTests(unittest.TestCase):
    def test_during_market_uses_big_order_net_inflow(self) -> None:
        """智能盯盘：始终用 大单流入−大单流出。"""
        stock = {
            "流通市值": 10_000_000_000.0,
            "个股资金流": {"大单流入": "500 万元", "大单流出": "100 万元"},
        }
        s, d = score_stock_fund_flow(stock, mode="during_market", today_s="2026-07-01")
        self.assertGreaterEqual(s, 80)
        self.assertEqual(d["来源"], "大单流入-大单流出")
        self.assertGreater(d["净额"], 0)

    def test_during_market_outflow_negative(self) -> None:
        stock = {
            "流通市值": 100_000_000_000.0,
            "个股资金流": {"大单流入": "100 万元", "大单流出": "500 万元"},
        }
        s, d = score_stock_fund_flow(stock, mode="during_market", today_s="2026-07-01")
        self.assertLess(s, 0)
        self.assertEqual(d["来源"], "大单流入-大单流出")
        self.assertGreater(d["流出基础惩罚"], 0)

    def test_evening_prefers_daily_today_main_net(self) -> None:
        """晚间复盘：日线最新一条为当天时，取 主力净流入-净额（元）。"""
        stock = {
            "流通市值": 48_000_000_000.0,
            "个股资金流": {"大单流入": "100 万元", "大单流出": "500 万元"},  # 大单净为负
            "个股资金流日线": [
                {"日期": "2026-06-30 00:00:00", "主力净流入-净额": -723243200.0},
                {"日期": "2026-07-01 00:00:00", "主力净流入-净额": 680005888.0},
            ],
        }
        s, d = score_stock_fund_flow(stock, mode="post_market_evening", today_s="2026-07-01")
        self.assertEqual(d["来源"], "日线-当日")
        # 净额以万元展示（保留2位），反算元误差 < 100
        self.assertAlmostEqual(d["净额"], 680005888.0 / 10_000.0, places=1)
        self.assertGreaterEqual(s, 80)  # 主力净流入 → 正分

    def test_evening_falls_back_to_big_order_when_no_today_daily(self) -> None:
        """晚间复盘：日线无当天数据 → 回退大单流入−大单流出。"""
        stock = {
            "流通市值": 10_000_000_000.0,
            "个股资金流": {"大单流入": "500 万元", "大单流出": "100 万元"},
            "个股资金流日线": [
                {"日期": "2026-06-30 00:00:00", "主力净流入-净额": -723243200.0},
            ],
        }
        s, d = score_stock_fund_flow(stock, mode="post_market_evening", today_s="2026-07-01")
        self.assertEqual(d["来源"], "大单流入-大单流出")
        self.assertGreater(d["净额"], 0)
        self.assertGreaterEqual(s, 80)

    def test_evening_falls_back_when_no_daily_at_all(self) -> None:
        stock = {
            "流通市值": 10_000_000_000.0,
            "个股资金流": {"大单流入": "500 万元", "大单流出": "100 万元"},
        }
        s, d = score_stock_fund_flow(stock, mode="post_market_evening", today_s="2026-07-01")
        self.assertEqual(d["来源"], "大单流入-大单流出")
        self.assertGreaterEqual(s, 80)

    def test_outflow_penalized_by_float_cap(self) -> None:
        small = {
            "流通市值": 10_000_000_000.0,
            "个股资金流": {"大单流入": "100 万元", "大单流出": "200 万元"},
        }
        large = {
            "流通市值": 2_000_000_000.0,
            "个股资金流": {"大单流入": "100 万元", "大单流出": "200 万元"},
        }
        s_small, _ = score_stock_fund_flow(small, mode="during_market", today_s="2026-07-01")
        s_large, d_large = score_stock_fund_flow(large, mode="during_market", today_s="2026-07-01")
        self.assertLess(s_small, 0)
        self.assertLess(s_large, 0)
        self.assertLess(s_large, s_small)  # 流通市值小 → 占比大 → 更重
        self.assertGreater(d_large["流出占流通市值"], 0.005)
        self.assertGreater(d_large["流出占比惩罚"], 0)

    def test_consecutive_outflow_extra_penalty(self) -> None:
        base = {
            "流通市值": 5_000_000_000.0,
            "个股资金流": {"大单流入": "100 万元", "大单流出": "500 万元"},
        }
        s1, _ = score_stock_fund_flow(base, mode="during_market", today_s="2026-07-01")
        streak = {
            **base,
            "个股资金流日线": [
                {"日期": "2026-06-29 00:00:00", "主力净流入-净额": -1e8},
                {"日期": "2026-06-30 00:00:00", "主力净流入-净额": -2e8},
                {"日期": "2026-07-01 00:00:00", "主力净流入-净额": -3e8},
            ],
        }
        # 注意：streak 用例的日线最新为当天(7/01)，晚间模式下会走日线主力净额(-3亿)路径
        s3, d = score_stock_fund_flow(streak, mode="post_market_evening", today_s="2026-07-01")
        self.assertGreaterEqual(d["连续流出天数"], 3)
        self.assertGreater(d["连续流出惩罚"], 0)
        self.assertLess(s3, s1)
        self.assertLess(s3, -40)

    def test_unit_parsing_yi_and_wan(self) -> None:
        """原始数据带单位：万 / 亿 均换算为元。"""
        stock = {
            "流通市值": 5_000_000_000.0,
            "个股资金流": {"大单流入": "0.8 亿", "大单流出": "0.3 亿"},
        }
        s, d = score_stock_fund_flow(stock, mode="during_market", today_s="2026-07-01")
        # 0.8亿 - 0.3亿 = 0.5亿 = 5000万元
        self.assertAlmostEqual(d["净额"], 5000.0, places=1)
        self.assertGreater(s, 0)

    def test_no_data_returns_neutral(self) -> None:
        stock = {"流通市值": 5_000_000_000.0, "个股资金流": {}}
        s, d = score_stock_fund_flow(stock, mode="post_market_evening", today_s="2026-07-01")
        self.assertEqual(s, 0)
        self.assertFalse(d.get("available", True))


if __name__ == "__main__":
    unittest.main()
