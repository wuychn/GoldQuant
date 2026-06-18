"""候选初筛在测试阶段的条数上限。"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.config import Settings
from quant.pool import candidate_sources


class CandidateTestPhaseLimitTests(unittest.IsolatedAsyncioTestCase):
    def _test_settings(self) -> Settings:
        s = MagicMock(spec=Settings)
        s.QUANT_TEST_PHASE = True
        s.quant_test_list_limit = lambda: 3
        s.quant_hot_list_limit = lambda: 3
        return s

    async def test_prefilter_zt_truncates_after_pool_filter(self) -> None:
        settings = self._test_settings()
        cfg = {"candidate": {"zt_min_boards": 3}}
        zt_rows = [{"股票代码": f"60000{i}", "连板数": 3} for i in range(10)]
        with patch(
            "quant.pool.candidate_sources.prefilter_zt_pool",
            return_value=zt_rows,
        ):
            out = await candidate_sources._prefilter_zt(settings, cfg, zt_rows=zt_rows)
        self.assertEqual(len(out), 3)

    async def test_prefilter_ths_rank_truncates_merged_rows(self) -> None:
        settings = self._test_settings()
        cfg = {"candidate": {"cxg_labels": ["创月新高"]}}
        big_batch = [{"股票代码": f"00000{i}", "股票简称": f"s{i}"} for i in range(20)]

        async def fake_fetch(label, fetch_fn, default, progress_scope):
            del label, fetch_fn, progress_scope
            return big_batch

        with patch(
            "quant.pool.candidate_sources.fetch_with_retry",
            side_effect=fake_fetch,
        ), patch(
            "quant.pool.candidate_sources.merge_ths_rank_from_batches",
            return_value=[{"股票代码": f"00000{i}"} for i in range(10)],
        ):
            out = await candidate_sources._prefilter_ths_rank(
                settings, cfg, progress_scope="test"
            )
        self.assertEqual(len(out), 3)


if __name__ == "__main__":
    unittest.main()
