"""资金流口径公共解析测试。"""

from __future__ import annotations

import unittest

from quant.market.fund_flow import (
    amount_to_yuan,
    daily_latest_today_net_yuan,
    intraday_big_net_yuan,
    intraday_main_net_wan,
    intraday_main_net_yuan,
    resolve_main_net_yuan,
)


class FundFlowParseTests(unittest.TestCase):
    def test_amount_to_yuan_units(self) -> None:
        self.assertEqual(amount_to_yuan("56055.12 万元"), 560551200.0)
        self.assertEqual(amount_to_yuan("0.8 亿"), 80_000_000.0)
        self.assertEqual(amount_to_yuan("-20031.34 万元"), -200313400.0)
        self.assertEqual(amount_to_yuan(680005888.0), 680005888.0)
        self.assertEqual(amount_to_yuan("32.1"), 32.1)
        self.assertIsNone(amount_to_yuan(None))
        self.assertIsNone(amount_to_yuan("—"))

    def test_intraday_big_net_yuan(self) -> None:
        flow = {"大单流入": "300800.23 万元", "大单流出": "263995.22 万元"}
        self.assertAlmostEqual(intraday_big_net_yuan(flow), 368050100.0, places=1)
        # 缺字段 → None
        self.assertIsNone(intraday_big_net_yuan({"大单流入": "100 万元"}))
        self.assertIsNone(intraday_big_net_yuan(None))

    def test_stock_level_helpers(self) -> None:
        stock = {"个股资金流": {"大单流入": "600 万元", "大单流出": "100 万元"}}
        self.assertEqual(intraday_main_net_yuan(stock), 5_000_000.0)
        self.assertEqual(intraday_main_net_wan(stock), 500.0)

    def test_daily_latest_today_only_when_date_matches(self) -> None:
        daily = [
            {"日期": "2026-06-30 00:00:00", "主力净流入-净额": -723243200.0},
            {"日期": "2026-07-01 00:00:00", "主力净流入-净额": 680005888.0},
        ]
        self.assertEqual(daily_latest_today_net_yuan(daily, "2026-07-01"), 680005888.0)
        # 当天不在最新一条 → None（回退大单净）
        self.assertIsNone(daily_latest_today_net_yuan(daily, "2026-07-02"))
        self.assertIsNone(daily_latest_today_net_yuan([], "2026-07-01"))

    def test_resolve_during_market_always_big_net(self) -> None:
        stock = {
            "个股资金流": {"大单流入": "500 万元", "大单流出": "100 万元"},
            "个股资金流日线": [{"日期": "2026-07-01 00:00:00", "主力净流入-净额": 680005888.0}],
        }
        net, src = resolve_main_net_yuan(stock, mode="during_market", today_s="2026-07-01")
        # 盘中忽略日线，始终用大单净
        self.assertEqual(net, 4_000_000.0)
        self.assertEqual(src, "大单流入-大单流出")

    def test_resolve_evening_prefers_daily_today(self) -> None:
        stock = {
            "个股资金流": {"大单流入": "100 万元", "大单流出": "500 万元"},  # 大单净 -400万
            "个股资金流日线": [{"日期": "2026-07-01 00:00:00", "主力净流入-净额": 680005888.0}],
        }
        net, src = resolve_main_net_yuan(stock, mode="post_market_evening", today_s="2026-07-01")
        self.assertEqual(net, 680005888.0)
        self.assertEqual(src, "日线-当日")

    def test_resolve_evening_falls_back_to_big_net(self) -> None:
        stock = {
            "个股资金流": {"大单流入": "500 万元", "大单流出": "100 万元"},
            "个股资金流日线": [{"日期": "2026-06-30 00:00:00", "主力净流入-净额": -723243200.0}],
        }
        net, src = resolve_main_net_yuan(stock, mode="post_market_evening", today_s="2026-07-01")
        self.assertEqual(net, 4_000_000.0)
        self.assertEqual(src, "大单流入-大单流出")

    def test_resolve_no_data(self) -> None:
        net, src = resolve_main_net_yuan({"个股资金流": {}}, mode="post_market_evening", today_s="2026-07-01")
        self.assertIsNone(net)
        self.assertEqual(src, "无数据")


if __name__ == "__main__":
    unittest.main()
