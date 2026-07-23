"""自选原因人类可读格式。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from quant.narrative.stock_lines import (
    build_watchlist_human_reason,
    build_watchlist_push_section,
    ensure_watchlist_reason_display,
    format_watchlist_reason_bullet,
    refresh_merged_watchlist_reasons,
)
from quant.candidates.ths_rank_util import format_ths_rank_tags_brief


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
            "所属概念": ["芯片", "半导体", "国产替代"],
            "人气排名": 1,
            "候选来源": ["人气榜"],
        }
        text = build_watchlist_human_reason(score, row)
        self.assertEqual(
            text,
            "北方华创，所属概念芯片、半导体、国产替代，同花顺人气榜第1，评分78",
        )

    def test_concept_fit_rank_top_three_in_reason(self) -> None:
        score = SimpleNamespace(name="风华高科", total=80.0, dimensions=[])
        row = {
            "股票代码": "000636",
            "股票名称": "风华高科",
            "概念粘合度": [
                {"rank": 1, "concept": "超级电容"},
                {"rank": 2, "concept": "储能"},
                {"rank": 3, "concept": "5G"},
            ],
            "所属概念": ["超级电容", "储能", "5G"],
        }
        text = build_watchlist_human_reason(score, row)
        self.assertEqual(text, "风华高科，所属概念超级电容、储能、5G，评分80")

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

    def test_push_section_price_range_filters_out_of_range(self) -> None:
        merged = [
            {
                "股票代码": "000001",
                "股票名称": "低价股",
                "评分": 80,
                "盘口": {"最新": 5.0},
                "加入自选原因": "低价股，评分80",
            },
            {
                "股票代码": "600519",
                "股票名称": "高价股",
                "评分": 78,
                "盘口": {"最新": 1500.0},
                "加入自选原因": "高价股，评分78",
            },
        ]
        with patch(
            "quant.config.load_push_config",
            return_value={"watchlist_price_range": {"enabled": True, "min": 10, "max": 100}},
        ):
            section = build_watchlist_push_section(merged, [], [])
        # 5 元低于下限 10、1500 元高于上限 100，均被过滤
        self.assertNotIn("低价股", section)
        self.assertNotIn("高价股", section)
        self.assertIn("暂无自选标的", section)

    def test_push_section_price_range_keeps_in_range(self) -> None:
        merged = [
            {
                "股票代码": "000001",
                "股票名称": "中价股",
                "评分": 80,
                "盘口": {"最新": 25.0},
                "加入自选原因": "中价股，评分80",
            },
        ]
        with patch(
            "quant.config.load_push_config",
            return_value={"watchlist_price_range": {"enabled": True, "min": 10, "max": 100}},
        ):
            section = build_watchlist_push_section(merged, [], [])
        self.assertIn("中价股", section)

    def test_push_section_price_range_via_price_by_code(self) -> None:
        merged = [
            {"股票代码": "000001", "股票名称": "无盘口股", "评分": 80,
             "加入自选原因": "无盘口股，评分80"},
        ]
        with patch(
            "quant.config.load_push_config",
            return_value={"watchlist_price_range": {"enabled": True, "min": 10, "max": 100}},
        ):
            # 行内无现价，依靠 price_by_code 命中
            section = build_watchlist_push_section(
                merged, [], [], price_by_code={"000001": 30.0}
            )
        self.assertIn("无盘口股", section)
        with patch(
            "quant.config.load_push_config",
            return_value={"watchlist_price_range": {"enabled": True, "min": 10, "max": 100}},
        ):
            section = build_watchlist_push_section(
                merged, [], [], price_by_code={"000001": 200.0}
            )
        self.assertNotIn("无盘口股", section)

    def test_push_section_no_filter_when_disabled(self) -> None:
        merged = [
            {"股票代码": "000001", "股票名称": "A股", "评分": 80,
             "盘口": {"最新": 5.0}, "加入自选原因": "A股，评分80"},
        ]
        with patch(
            "quant.config.load_push_config",
            return_value={"watchlist_price_range": {"enabled": False}},
        ):
            section = build_watchlist_push_section(merged, [], [])
        self.assertIn("A股", section)

    def test_push_section_score_filter_drops_below_threshold(self) -> None:
        """当天评分 < min_score 的标的不推送（hysteresis 死区保留但不展示）。"""
        merged = [
            {"股票代码": "000001", "股票名称": "达标股", "评分": 80,
             "加入自选原因": "达标股，评分80"},
            {"股票代码": "000002", "股票名称": "死区股", "评分": 68,
             "加入自选原因": "死区股，评分68"},
        ]
        with patch(
            "quant.config.load_push_config",
            return_value={
                "watchlist_price_range": {"enabled": False},
                "watchlist_score_filter": {"enabled": True, "min_score": 70},
            },
        ):
            section = build_watchlist_push_section(merged, [], [])
        self.assertIn("达标股", section)
        self.assertNotIn("死区股", section)

    def test_push_section_score_filter_default_uses_watchlist_threshold(self) -> None:
        """min_score 未配置时默认取 scoring.watchlist_threshold（70）。"""
        merged = [
            {"股票代码": "000001", "股票名称": "刚好达标", "评分": 70,
             "加入自选原因": "刚好达标，评分70"},
            {"股票代码": "000002", "股票名称": "未达标", "评分": 69,
             "加入自选原因": "未达标，评分69"},
        ]
        with patch(
            "quant.config.load_push_config",
            return_value={"watchlist_score_filter": {"enabled": True}},
        ), patch(
            "quant.config.load_scoring_config",
            return_value={"watchlist_threshold": 70},
        ):
            section = build_watchlist_push_section(merged, [], [])
        self.assertIn("刚好达标", section)
        self.assertNotIn("未达标", section)

    def test_format_watchlist_reason_bullet_fallback(self) -> None:
        row = {"股票代码": "000001", "股票名称": "平安银行", "评分": 72}
        self.assertIn("平安银行", format_watchlist_reason_bullet(row))

    def test_legacy_reason_gets_name_prefix(self) -> None:
        row = {
            "股票代码": "603228",
            "股票名称": "景旺电子",
            "评分": 79.1,
            "加入自选原因": "评分79.1；创新高(一年新高、创月新高)",
        }
        bullet = format_watchlist_reason_bullet(row)
        self.assertIn("景旺电子", bullet)
        self.assertTrue(bullet.startswith("· 景旺电子"))

    def test_refresh_merged_rewrites_legacy_reason(self) -> None:
        score = SimpleNamespace(
            name="景旺电子",
            total=79.1,
            code="603228",
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
                "股票代码": "603228",
                "股票名称": "景旺电子",
                "评分": 79.1,
                "加入自选原因": "评分79.1；创新高(一年新高、创月新高)",
            }
        ]
        refresh_merged_watchlist_reasons(
            merged,
            score_by_code={"603228": score},
            candidate_by_code={
                "603228": {
                    "股票代码": "603228",
                    "股票名称": "景旺电子",
                    "榜单标签": ["一年新高", "创月新高"],
                }
            },
        )
        self.assertEqual(
            merged[0]["加入自选原因"],
            "景旺电子，所属行业元件，创新高，评分79",
        )

    def test_refresh_merged_without_score_still_prefixes_name(self) -> None:
        merged = [
            {
                "股票代码": "603228",
                "股票名称": "景旺电子",
                "评分": 78.6,
                "加入自选原因": "评分78.6；涨停池(连板3)",
            }
        ]
        refresh_merged_watchlist_reasons(
            merged,
            score_by_code={},
            candidate_by_code={},
        )
        self.assertEqual(
            merged[0]["加入自选原因"],
            "景旺电子，评分78.6；涨停池(连板3)",
        )

    def test_ensure_watchlist_reason_display(self) -> None:
        row = {
            "股票名称": "中天科技",
            "加入自选原因": "评分78.1；人气榜(排名10)",
        }
        self.assertEqual(
            ensure_watchlist_reason_display(row),
            "中天科技，评分78.1；人气榜(排名10)",
        )


class ThsRankBriefTests(unittest.TestCase):
    def test_format_ths_rank_tags_brief(self) -> None:
        tags = format_ths_rank_tags_brief(["创月新高", "半年新高", "量价齐升"])
        self.assertEqual(tags, ["创新高", "量价齐升"])


if __name__ == "__main__":
    unittest.main()
