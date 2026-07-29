"""merge_prefiltered_sources 测试阶段截断。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from quant.pool.source_merge import merge_prefiltered_sources


class SourceMergeTestPhaseTests(unittest.TestCase):
    @patch("common.testing.trim.truncate_list_for_test_phase")
    def test_merge_respects_test_phase_cap(self, mock_truncate) -> None:
        rows_a = [{"股票代码": "600001", "候选来源": "人气榜"}]
        rows_b = [{"股票代码": "600002", "候选来源": "涨停池"}]
        merged = rows_a + rows_b
        mock_truncate.side_effect = lambda r, settings=None: r[:1]

        out, orders = merge_prefiltered_sources(rows_a, rows_b)

        mock_truncate.assert_called_once()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["股票代码"], "600001")
        self.assertEqual(orders["人气榜"], ["600001"])
        self.assertEqual(orders["涨停池"], [])


if __name__ == "__main__":
    unittest.main()
