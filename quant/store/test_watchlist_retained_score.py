"""保留自选补算评分测试。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from quant.narrative.stock_lines import refresh_merged_watchlist_reasons
from quant.store.watchlist import (
    index_enriched_watchlist,
    supplement_retained_watchlist_scores,
)


class RetainedWatchlistScoreTests(unittest.TestCase):
    def test_index_enriched_watchlist(self) -> None:
        payload = {
            "自选股": [
                {"股票代码": "000636", "股票名称": "风华高科", "盘口": {"最新": 72.0}},
            ]
        }
        idx = index_enriched_watchlist(payload)
        self.assertIn("000636", idx)
        self.assertEqual(idx["000636"]["股票名称"], "风华高科")

    def test_supplement_scores_retained_not_in_candidate_pool(self) -> None:
        scored = SimpleNamespace(code="000636", name="风华高科", total=52.94, dimensions=[])
        engine = MagicMock()
        engine.score_stock.return_value = scored
        ctx = MagicMock()
        merged = [
            {
                "股票代码": "000636",
                "股票名称": "风华高科",
                "评分": 72.69,
                "加入自选原因": "旧原因",
                "最后入选日期": "2026-06-17",
            }
        ]
        scores: list = []
        score_by_code: dict = {}
        enriched = {
            "000636": {
                "股票代码": "000636",
                "股票名称": "风华高科",
                "盘口": {"最新": 72.0, "涨幅": -1.0},
            }
        }
        n = supplement_retained_watchlist_scores(
            ctx,
            engine,
            merged,
            scores=scores,
            score_by_code=score_by_code,
            enriched_by_code=enriched,
        )
        self.assertEqual(n, 1)
        self.assertEqual(score_by_code["000636"], scored)
        self.assertEqual(scores, [scored])
        engine.score_stock.assert_called_once_with(ctx, enriched["000636"])

    def test_supplement_skips_already_scored_candidate(self) -> None:
        existing = SimpleNamespace(code="600522", name="中天科技", total=80.0, dimensions=[])
        engine = MagicMock()
        ctx = MagicMock()
        merged = [{"股票代码": "600522", "股票名称": "中天科技", "评分": 80.0}]
        scores = [existing]
        score_by_code = {"600522": existing}
        n = supplement_retained_watchlist_scores(
            ctx,
            engine,
            merged,
            scores=scores,
            score_by_code=score_by_code,
            enriched_by_code={"600522": {"股票代码": "600522"}},
        )
        self.assertEqual(n, 0)
        engine.score_stock.assert_not_called()

    def test_refresh_after_supplement_updates_score(self) -> None:
        score = SimpleNamespace(
            name="风华高科",
            total=52.94,
            dimensions=[
                SimpleNamespace(
                    name="concept_theme",
                    available=True,
                    detail={"最佳赛道": "行业", "最佳命中概念": "元件"},
                )
            ],
        )
        merged = [
            {
                "股票代码": "000636",
                "股票名称": "风华高科",
                "评分": 72.69,
                "加入自选原因": "旧原因",
            }
        ]
        refresh_merged_watchlist_reasons(
            merged,
            score_by_code={"000636": score},
            candidate_by_code={
                "000636": {
                    "股票代码": "000636",
                    "股票名称": "风华高科",
                    "行业": "元件",
                }
            },
        )
        self.assertEqual(merged[0]["评分"], 52.94)
        self.assertIn("元件", merged[0]["加入自选原因"])
        self.assertIn("53", merged[0]["加入自选原因"])


if __name__ == "__main__":
    unittest.main()
