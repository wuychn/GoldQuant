"""新闻格式化测试。"""

from __future__ import annotations

import unittest

from quant.narrative.news_format import format_news_for_push, strip_wrapped_parens


class TestNewsFormat(unittest.TestCase):
    def test_strip_parens(self) -> None:
        self.assertEqual(strip_wrapped_parens("（沪指收涨）"), "沪指收涨")

    def test_format_removes_parens_and_truncated_tail(self) -> None:
        raw = (
            "1. （A股收涨）\n"
            "2. （商务部数据）\n"
            "24. 黄仁勋反对美封禁中国AI模型，市场误解了DeepSeek和\n"
            "综合解读：（市场偏强）"
        )
        out = format_news_for_push(raw)
        self.assertNotIn("（A股收涨）", out)
        self.assertIn("1. A股收涨", out)
        self.assertNotIn("DeepSeek和", out)
        self.assertIn("综合解读：", out)


if __name__ == "__main__":
    unittest.main()
