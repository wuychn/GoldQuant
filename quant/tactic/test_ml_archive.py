import unittest

import pandas as pd

from quant.data.sources.eastmoney.lhb import normalize_lhb
from quant.tactic.ml_archive import absent_lhb, fund_row


class LhbArchiveTests(unittest.TestCase):
    def test_keeps_same_day(self):
        df = pd.DataFrame(
            [
                {
                    "代码": "002343",
                    "名称": "慈文传媒",
                    "上榜日": "2026-10-20",
                    "上榜原因": "日涨幅偏离值达到7%",
                    "收盘价": 10.25,
                    "涨跌幅": -0.1,
                    "龙虎榜净买额": 1.0,
                },
                {
                    "代码": "002242",
                    "名称": "九阳股份",
                    "上榜日": "2026-10-19",
                    "上榜原因": "其他",
                },
            ]
        )
        out = normalize_lhb(df, as_of="2026-10-20")
        self.assertEqual(list(out["code"]), ["002343"])
        self.assertTrue(bool(out.iloc[0]["on_list"]))
        self.assertEqual(out.iloc[0]["reason"], "日涨幅偏离值达到7%")

    def test_absent_and_fund_row(self):
        row = absent_lhb("002242", "2026-10-20", "九阳股份")
        self.assertFalse(row["on_list"])
        self.assertEqual(row["code"], "002242")
        hit = fund_row(
            "002343",
            "2026-10-20",
            "慈文传媒",
            [
                {"日期": "2026-10-19", "主力净流入-净额": 1},
                {
                    "日期": "2026-10-20",
                    "主力净流入-净额": 9,
                    "超大单净流入-净额": 3,
                    "主力净流入-净占比": 12.5,
                },
            ],
        )
        self.assertEqual(hit["main_net"], 9.0)
        self.assertEqual(hit["super_net"], 3.0)
        self.assertEqual(hit["main_pct"], 12.5)
        miss = fund_row("002242", "2026-10-20", "九阳股份", None)
        self.assertIsNone(miss["main_net"])


if __name__ == "__main__":
    unittest.main()
