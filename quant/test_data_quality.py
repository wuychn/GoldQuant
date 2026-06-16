"""数据新鲜度门禁测试。"""

from __future__ import annotations

import unittest

from quant.data_quality import assess_payload_quality, skip_intraday_buy_codes


def _minute_bars(n: int = 6) -> list[dict]:
    return [
        {
            "时间": f"2026-05-26 09:{30 + i}:00",
            "开盘": 10.5,
            "收盘": 10.5 + i * 0.01,
            "成交量": 1000 + i,
        }
        for i in range(n)
    ]


class DataQualityTests(unittest.TestCase):
    def test_ok_payload(self) -> None:
        payload = {
            "大盘指数": [{"代码": "000001", "涨跌幅": 0.5}],
            "赚钱效应": {"上涨": 2000, "下跌": 1500},
            "自选股": [
                {
                    "股票代码": "600000",
                    "盘口": {"最新": 10.5},
                    "分钟行情": _minute_bars(),
                }
            ],
        }
        r = assess_payload_quality(payload, mode="during_market")
        self.assertTrue(r.ok)
        self.assertFalse(r.block_intraday_buy)
        self.assertEqual(r.skip_intraday_buy_codes, [])

    def test_missing_index_blocks_intraday(self) -> None:
        payload = {"自选股": [{"股票代码": "600000", "盘口": {"最新": 10.5}}]}
        r = assess_payload_quality(payload, mode="during_market")
        self.assertTrue(r.block_intraday_buy)
        self.assertTrue(r.block_execute)

    def test_bad_minute_bars_skip_only_that_code(self) -> None:
        payload = {
            "大盘指数": [{"代码": "000001", "涨跌幅": 0.5}],
            "赚钱效应": {"上涨": 2000, "下跌": 1500},
            "自选股": [
                {
                    "股票代码": "603065",
                    "盘口": {"最新": 10.5},
                    "分钟行情": [],
                },
                {
                    "股票代码": "000636",
                    "盘口": {"最新": 41.0},
                    "分钟行情": _minute_bars(),
                },
            ],
        }
        r = assess_payload_quality(payload, mode="during_market")
        self.assertFalse(r.block_intraday_buy)
        self.assertFalse(r.block_execute)
        self.assertEqual(r.skip_intraday_buy_codes, ["603065"])
        self.assertIn("603065", r.stock_issues)
        self.assertNotIn("000636", r.skip_intraday_buy_codes)
        payload["_data_quality"] = r.to_dict()
        self.assertEqual(skip_intraday_buy_codes(payload), {"603065"})


if __name__ == "__main__":
    unittest.main()
