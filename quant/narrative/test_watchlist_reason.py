"""自选原因人类可读格式。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from quant.narrative.stock_lines import (
    build_watchlist_human_reason,
    build_watchlist_push_section,
    format_watchlist_reason_bullet,
)
from quant.pool.ths_rank_util import format_ths_rank_tags_brief


class WatchlistReasonTests(unittest.TestCase):
    def test_industry_and_cxg_reason(self) -> None:
        score = SimpleNamespace(
            name="国瓷材料",
            total=80.2,
            dimensions=[
                SimpleNamespace(
                    name="concept_theme",
                    available=True,
                    detail={"最佳赛道": "行业", "最佳命中概念": "元件"},
                )
            ],
        )
        row = {
            "股票代码": "300285",
            "股票名称": "国瓷材料",
            "行业": "元件",
            "榜单标签": ["创月新高"],
            "候选来源": ["形态榜"],
        }
        text = build_watchlist_human_reason(score, row)
        self.assertEqual(text, "国瓷材料，所属行业元件，创新高，评分80")

    def test_concept_and_popularity_reason(self) -> None:
        score = SimpleNamespace(
            name="北方华创",
            total=78.4,
            dimensions=[
                SimpleNamespace(
                    name="concept_theme",
                    available=True,
                    detail={"最佳赛道": "概念", "最佳命中概念": "芯片"},
                )
            ],
        )
        row = {
            "股票代码": "002371",
            "股票名称": "北方华创",
            "所属概念": "芯片",
            "人气排名": 1,
            "候选来源": ["人气榜"],
        }
        text = build_watchlist_human_reason(score, row)
        self.assertEqual(text, "北方华创，所属概念芯片，同花顺人气榜第1，评分78")

    def test_push_section_uses_reason_bullets(self) -> None:
        merged = [
            {
                "股票代码": "300285",
                "股票名称": "国瓷材料",
                "评分": 80,
                "加入自选原因": "国瓷材料，所属行业元件，创新高，评分80",
            }
        ]
        section = build_watchlist_push_section(merged, [], [])
        self.assertIn("· 国瓷材料，所属行业元件，创新高，评分80", section)

    def test_format_watchlist_reason_bullet_fallback(self) -> None:
        row = {"股票代码": "000001", "股票名称": "平安银行", "评分": 72}
        self.assertIn("平安银行", format_watchlist_reason_bullet(row))


class ThsRankBriefTests(unittest.TestCase):
    def test_format_ths_rank_tags_brief(self) -> None:
        tags = format_ths_rank_tags_brief(["创月新高", "半年新高", "量价齐升"])
        self.assertEqual(tags, ["创新高", "量价齐升"])


if __name__ == "__main__":
    unittest.main()
