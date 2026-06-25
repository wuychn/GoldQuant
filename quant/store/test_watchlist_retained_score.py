"""保留自选补算评分测试。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from quant.narrative.stock_lines import refresh_merged_watchlist_reasons
from quant.store.watchlist import (
    apply_watchlist_fail_streak,
    index_enriched_watchlist,
    merge_watchlist_evening,
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


class WatchlistFailStreakTests(unittest.TestCase):
    def test_merge_keeps_existing_not_in_passed(self) -> None:
        existing = [
            {
                "股票代码": "000636",
                "股票名称": "风华高科",
                "评分": 72.0,
                "最后入选日期": "2026-06-10",
                "未达标连续天数": 2,
            }
        ]
        merged, added, removed = merge_watchlist_evening(
            existing,
            [{"股票代码": "600522", "股票名称": "中天科技", "评分": 85.0}],
            today=__import__("datetime").date(2026, 6, 23),
        )
        self.assertEqual(removed, [])
        self.assertEqual(len(added), 1)
        codes = {r["股票代码"] for r in merged}
        self.assertEqual(codes, {"000636", "600522"})
        old = next(r for r in merged if r["股票代码"] == "000636")
        self.assertEqual(old["未达标连续天数"], 2)

    def test_fail_streak_resets_when_passes(self) -> None:
        score = SimpleNamespace(total=75.0)
        merged = [{"股票代码": "000636", "未达标连续天数": 3}]
        kept, to_observe = apply_watchlist_fail_streak(
            merged, score_by_code={"000636": score}, threshold=70, max_streak=5
        )
        self.assertEqual(to_observe, [])
        self.assertEqual(kept[0]["未达标连续天数"], 0)

    def test_fail_streak_moves_to_observe_after_limit(self) -> None:
        score = SimpleNamespace(total=60.0)
        merged = [{"股票代码": "000636", "未达标连续天数": 2}]
        kept, to_observe = apply_watchlist_fail_streak(
            merged, score_by_code={"000636": score}, threshold=70, max_streak=3
        )
        self.assertEqual(kept, [])
        self.assertEqual(len(to_observe), 1)
        self.assertEqual(to_observe[0]["未达标连续天数"], 0)
        self.assertIn("进入观察池日期", to_observe[0])

    def test_merge_existing_passed_not_counted_as_added(self) -> None:
        existing = [
            {"股票代码": "600487", "股票名称": "亨通光电", "评分": 72.0},
            {"股票代码": "SH600522", "股票名称": "中天科技", "评分": 80.0},
        ]
        passed = [
            {"股票代码": "600487", "股票名称": "亨通光电", "评分": 78.0},
            {"股票代码": "000636", "股票名称": "风华高科", "评分": 85.0},
        ]
        merged, added, _ = merge_watchlist_evening(
            existing, passed, today=__import__("datetime").date(2026, 6, 23)
        )
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["股票代码"], "000636")
        codes = {r["股票代码"] for r in merged}
        self.assertEqual(codes, {"600487", "600522", "000636"})

    def test_fail_streak_keeps_when_no_score(self) -> None:
        merged = [{"股票代码": "000636", "未达标连续天数": 4}]
        kept, to_observe = apply_watchlist_fail_streak(
            merged, score_by_code={}, threshold=70, max_streak=5
        )
        self.assertEqual(to_observe, [])
        self.assertEqual(kept[0]["未达标连续天数"], 4)


if __name__ == "__main__":
    unittest.main()
