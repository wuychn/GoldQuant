"""name PIT 快照 + ST 前缀匹配测试（批次2）。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class NamePitTests(unittest.TestCase):
    def setUp(self):
        import quant.data.store as st

        self._tmp = Path(tempfile.mkdtemp(prefix="gq_name_"))
        self._orig = st.quant_home
        st.quant_home = lambda: self._tmp  # type: ignore[assignment]

    def tearDown(self):
        import quant.data.store as st

        st.quant_home = self._orig  # type: ignore[assignment]

    def test_st_prefix_not_substring(self):
        from quant.data.universe import _is_st_name

        # ST/*ST 前缀 / 退市标识命中
        self.assertTrue(_is_st_name("ST花王"))
        self.assertTrue(_is_st_name("*ST测试"))
        self.assertTrue(_is_st_name("中弘退"))
        # 旧子串匹配会误命中含 "ST" 的名称；前缀匹配不会
        self.assertFalse(_is_st_name("FIRST"))
        self.assertFalse(_is_st_name("BEST"))
        self.assertFalse(_is_st_name("东方证券"))
        self.assertFalse(_is_st_name(""))

    def test_name_snapshot_roundtrip(self):
        from quant.data.store import read_name_snapshot, write_name_snapshot

        write_name_snapshot("2025-06-01", {"000001": "平安银行", "000002": "*ST测试"})
        m = read_name_snapshot("2025-06-01")
        self.assertEqual(m["000001"], "平安银行")
        self.assertEqual(m["000002"], "*ST测试")
        # 缺失日返回空 dict
        self.assertEqual(read_name_snapshot("2099-01-01"), {})


if __name__ == "__main__":
    unittest.main()
