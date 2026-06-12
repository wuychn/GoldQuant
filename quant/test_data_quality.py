"""数据新鲜度门禁测试。"""

from __future__ import annotations

import unittest

from quant.data_quality import assess_payload_quality


class DataQualityTests(unittest.TestCase):
    def test_ok_payload(self) -> None:
        payload = {
            "大盘指数": [{"代码": "000001", "涨跌幅": 0.5}],
            "赚钱效应": {"上涨": 2000, "下跌": 1500},
            "自选股": [
                {
                    "股票代码": "600000",
                    "盘口": {"最新": 10.5},
                    "分钟行情": [
                        {"时间": f"09:{30 + i}", "收盘": 10.5, "成交量": 1000 + i}
                        for i in range(6)
                    ],
                }
            ],
        }
        r = assess_payload_quality(payload, mode="during_market")
        self.assertTrue(r.ok)

    def test_missing_index_blocks_intraday(self) -> None:
        payload = {"自选股": [{"股票代码": "600000", "盘口": {"最新": 10.5}}]}
        r = assess_payload_quality(payload, mode="during_market")
        self.assertTrue(r.block_intraday_buy)
        self.assertTrue(r.block_execute)


if __name__ == "__main__":
    unittest.main()
