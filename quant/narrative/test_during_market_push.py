"""智能盯盘模板推送测试。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from quant.narrative.during_market_push import build_during_market_push
from quant.signals.models import TradeSignal

_FIXTURE = Path(__file__).resolve().parents[2] / "data" / "during_market.json"


class DuringMarketPushTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        raw = json.loads(_FIXTURE.read_text(encoding="utf-8"))
        cls.payload = raw.get("data") or raw

    def test_header_and_sections(self) -> None:
        text = build_during_market_push(
            self.payload,
            timestamp="2026-06-18 14:35:00",
        )
        self.assertIn("📡 智能盯盘 14:35  2026-06-18", text)
        self.assertIn("━━━━ 大盘 ━━━━", text)
        self.assertIn("上证", text)
        self.assertIn("━━━━ 概念·流入 TOP10 ━━━━", text)
        self.assertIn("━━━━ 自选股 ━━━━", text)
        self.assertIn("━━━━ 持仓股 ━━━━", text)
        self.assertIn("🚨 买卖信号", text)

    def test_cn_market_color_convention(self) -> None:
        text = build_during_market_push(
            {
                "大盘指数": [
                    {"代码": "000001", "最新价": 3372.15, "涨跌幅": -0.82},
                    {"代码": "399001", "最新价": 11089.42, "涨跌幅": 1.15},
                ],
                "赚钱效应": {"成交额": {"今日累计": "5823亿", "较昨日同时段": "放量623亿"}},
                "概念板块": {},
                "行业板块": {},
                "自选股": [],
                "持仓股": [],
            },
            timestamp="2026-06-18 14:35:00",
        )
        self.assertIn("上证 3372.15  🟢 -0.82%", text)
        self.assertIn("深证 11089.42  🔴 +1.15%", text)
        self.assertIn("成交 5823亿", text)

    def test_signals_block(self) -> None:
        text = build_during_market_push(
            {"自选股": [], "持仓股": []},
            timestamp="2026-06-18 14:35:00",
            raw_buy=[
                TradeSignal(
                    action="买入",
                    code="601689",
                    name="拓普集团",
                    price=67.10,
                    quantity=100,
                    strategy="主升浪",
                    reason="[上升途中]评分75",
                    signal_kind="上升途中",
                )
            ],
            raw_sell=[
                TradeSignal(
                    action="卖出",
                    code="600519",
                    name="贵州茅台",
                    price=1680.0,
                    quantity=100,
                    strategy="主升浪",
                    reason="日内走弱",
                    sell_type="日内走弱",
                )
            ],
        )
        self.assertIn("🔴 买入：拓普集团", text)
        self.assertIn("🟢 卖出：贵州茅台", text)


if __name__ == "__main__":
    unittest.main()
