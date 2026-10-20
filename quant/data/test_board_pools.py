"""炸板 / 跌停 / 昨日涨停归一化。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from quant.data.board_pools import (
    KIND_COLUMNS,
    normalize_board_pool,
    read_board_pool,
    write_board_pool,
)
from quant.store.paths import override_quant_home


class NormalizeBoardPoolTests(unittest.TestCase):
    def test_zb(self):
        raw = pd.DataFrame(
            {
                "代码": ["1"],
                "名称": ["平安银行"],
                "涨跌幅": [9.5],
                "最新价": [10.0],
                "涨停价": [11.0],
                "首次封板时间": ["093000"],
                "炸板次数": [2],
                "所属行业": ["银行"],
            }
        )
        out = normalize_board_pool(raw, kind="zb", as_of="2024-01-03")
        self.assertEqual(list(out.columns), list(KIND_COLUMNS["zb"]))
        self.assertEqual(out.iloc[0]["code"], "000001")
        self.assertEqual(int(out.iloc[0]["break_n"]), 2)

    def test_roundtrip(self):
        with override_quant_home(Path(tempfile.mkdtemp())):
            df = normalize_board_pool(
                pd.DataFrame({"代码": ["000001"], "名称": ["A"], "涨跌幅": [-10.0]}),
                kind="dt",
                as_of="2024-01-03",
            )
            n = write_board_pool("dt", "2024-01-03", df)
            self.assertEqual(n, 1)
            got = read_board_pool("dt", "2024-01-03")
            self.assertEqual(got.iloc[0]["code"], "000001")
