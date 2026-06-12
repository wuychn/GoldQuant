"""涨停池初筛测试。"""

from __future__ import annotations

import unittest

from quant.pool.sources import prefilter_zt_pool


class ZtPoolPrefilterTests(unittest.TestCase):
    def test_min_three_boards(self) -> None:
        rows = [
            {"代码": "600001", "名称": "A", "连板数": 1},
            {"代码": "600002", "名称": "B", "连板数": 2},
            {"代码": "600003", "名称": "C", "连板数": 3},
            {"代码": "600004", "名称": "D", "连板数": 5},
        ]
        cfg = {"zt_min_boards": 3}
        out = prefilter_zt_pool(rows, cfg=cfg)
        codes = {r["股票代码"] for r in out}
        self.assertEqual(codes, {"600003", "600004"})


if __name__ == "__main__":
    unittest.main()
