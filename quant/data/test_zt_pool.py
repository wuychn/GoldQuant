"""涨停池归一化与 PIT 快照。"""

from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from quant.data.zt_pool import (
    ZT_POOL_COLUMNS,
    normalize_zt_pool,
    read_zt_pool,
    write_zt_pool_snapshot,
)
from quant.store.paths import override_quant_home


class NormalizeZtPoolTests(unittest.TestCase):
    def test_maps_em_columns(self):
        raw = pd.DataFrame(
            {
                "代码": ["1", "600519"],
                "名称": ["平安银行", "贵州茅台"],
                "涨跌幅": [10.0, 10.0],
                "最新价": [11.0, 1400.0],
                "成交额": [1e9, 2e9],
                "流通市值": [1e11, 2e12],
                "总市值": [1e11, 2e12],
                "换手率": [3.0, 1.0],
                "封板资金": [1e8, 2e8],
                "首次封板时间": ["093000", "093100"],
                "最后封板时间": ["093500", "093200"],
                "炸板次数": [0, 1],
                "涨停统计": ["1/1", "2/2"],
                "连板数": [1, 2],
                "所属行业": ["银行", "食品饮料"],
            }
        )
        out = normalize_zt_pool(raw, as_of="2024-01-03")
        self.assertEqual(list(out.columns), list(ZT_POOL_COLUMNS))
        self.assertEqual(out["code"].tolist(), ["000001", "600519"])
        self.assertEqual(out["date"].tolist(), ["2024-01-03", "2024-01-03"])
        self.assertEqual(int(out["boards"].iloc[1]), 2)

    def test_empty(self):
        out = normalize_zt_pool(None, as_of="2024-01-03")
        self.assertTrue(out.empty)
        self.assertEqual(list(out.columns), list(ZT_POOL_COLUMNS))


class SnapshotTests(unittest.TestCase):
    def test_roundtrip(self):
        with override_quant_home(Path(self._tmp())):
            df = normalize_zt_pool(
                pd.DataFrame(
                    {
                        "代码": ["000001"],
                        "名称": ["平安银行"],
                        "涨跌幅": [10.0],
                        "最新价": [11.0],
                        "连板数": [1],
                    }
                ),
                as_of="2024-01-03",
            )
            n = write_zt_pool_snapshot("2024-01-03", df)
            self.assertEqual(n, 1)
            got = read_zt_pool("2024-01-03")
            self.assertEqual(got.iloc[0]["code"], "000001")
            self.assertTrue(read_zt_pool("2024-01-02").empty)

    def _tmp(self) -> str:
        import tempfile

        return tempfile.mkdtemp()


    def test_reconstruct_consecutive_boards(self):
        df = pd.DataFrame(
            {
                "code": ["000001", "000001", "000001"],
                "date": ["2024-01-02", "2024-01-03", "2024-01-04"],
                "name": ["平安", "平安", "平安"],
                "open": [10.0, 11.0, 12.1],
                "high": [11.0, 12.1, 12.2],
                "low": [10.0, 11.0, 11.5],
                "close": [11.0, 12.1, 12.0],
                "pre_close": [10.0, 11.0, 12.1],
                "amount": [1e9, 1e9, 1e9],
            }
        )
        from quant.data.zt_pool import zt_rows_from_daily_frame

        out = zt_rows_from_daily_frame(df)
        self.assertEqual(out["date"].tolist(), ["2024-01-02", "2024-01-03"])
        self.assertEqual(out["boards"].tolist(), [1, 2])
    def test_eastmoney_has_method_sina_raises(self):
        from quant.data.sources.market.eastmoney import EastmoneyMarketSource
        from quant.data.sources.market.sina import SinaMarketSource

        em = EastmoneyMarketSource()
        self.assertTrue(callable(em.fetch_zt_pool_hist))
        sina = SinaMarketSource()
        with self.assertRaises(NotImplementedError):
            sina.fetch_zt_pool_hist(date="2024-01-03")


if __name__ == "__main__":
    unittest.main()
