"""智能盯盘模板推送测试。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from quant.execution.executor import ExecutedTrade
from quant.narrative.during_market_push import build_during_market_push
from quant.scoring.context import ScoreContext
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
        self.assertIn("📡 盘中 14:35", text)
        self.assertIn("周四", text)
        self.assertIn("📊 上证", text)
        self.assertIn("💡 概念（流入/流出/涨跌）", text)
        self.assertIn("👀 自选异动", text)
        self.assertIn("💼 持仓（", text)
        self.assertIn("🚨 买卖信号", text)

    def test_cn_market_color_convention(self) -> None:
        payload = {
            "大盘指数": [
                {"代码": "000001", "最新价": 3372.15, "涨跌幅": -0.82},
                {"代码": "399001", "最新价": 11089.42, "涨跌幅": 1.15},
            ],
            "赚钱效应": {"成交额": {"今日累计": "5823亿", "较昨日同时段": "放量623亿"}},
            "概念板块": {},
            "行业板块": {},
            "自选股": [],
            "持仓股": [],
        }
        with patch("quant.store.state.get_holdings", return_value=[]):
            text = build_during_market_push(
                payload,
                timestamp="2026-06-18 14:35:00",
            )
        self.assertIn("上证", text)
        self.assertIn("-0.8%", text)
        self.assertIn("+1.1%", text)
        self.assertIn("🟢", text)
        self.assertIn("🔴", text)
        self.assertIn("成交5823", text)

    def test_signals_block(self) -> None:
        sell_sig = TradeSignal(
            action="卖出",
            code="600519",
            name="贵州茅台",
            price=1680.0,
            quantity=100,
            strategy="主升浪",
            reason="日内走弱",
            sell_type="日内走弱",
        )
        buy_sig = TradeSignal(
            action="买入",
            code="601689",
            name="拓普集团",
            price=67.10,
            quantity=100,
            strategy="主升浪",
            reason="[上升途中]评分75",
            signal_kind="上升途中",
        )
        with patch("quant.store.state.get_holdings", return_value=[]):
            pending = build_during_market_push(
                {"自选股": [], "持仓股": []},
                timestamp="2026-06-18 14:35:00",
                raw_buy=[buy_sig],
                raw_sell=[sell_sig],
            )
            executed = build_during_market_push(
                {"自选股": [], "持仓股": []},
                timestamp="2026-06-18 14:37:00",
                raw_sell=[sell_sig],
                executable=[sell_sig],
                executed=[
                    ExecutedTrade(
                        signal=sell_sig,
                        timestamp="14:37:00",
                        pnl=-120.0,
                    )
                ],
            )
        self.assertIn("拓普集团", pending)
        self.assertIn("贵州茅台", pending)
        self.assertIn("买信号·确认中", pending)
        self.assertIn("卖信号·确认中", pending)
        self.assertIn("100股", pending)
        self.assertIn("已卖", executed)
        self.assertIn("100股", executed)
        self.assertNotIn("卖信号·确认中", executed)

    def test_pending_sell_not_shown_as_skip(self) -> None:
        ctx = ScoreContext.from_payload(
            {
                "自选股": [],
                "持仓股": [
                    {
                        "股票代码": "600498",
                        "股票名称": "烽火通信",
                        "买入价": 80.0,
                        "持仓股数": 500,
                        "盘口": {"最新": 75.61, "涨幅": -2.0},
                    },
                    {
                        "股票代码": "002436",
                        "股票名称": "兴森科技",
                        "买入价": 47.7,
                        "持仓股数": 100,
                        "盘口": {"最新": 47.7, "涨幅": 0.0},
                    },
                ],
            },
            mode="during_market",
        )
        sell_sig = TradeSignal(
            action="卖出",
            code="600498",
            name="烽火通信",
            price=75.61,
            quantity=500,
            strategy="主升浪",
            reason="距日内高点回撤3.2%",
            sell_type="日内走弱",
            signal_kind="日内走弱",
        )
        audit = [
            {
                "股票代码": "600498",
                "股票名称": "烽火通信",
                "方向": "卖出",
                "信号类型": "日内走弱",
                "确认次数": 1,
                "状态": "当日锁存确认中（累计1/2次，0/10分）",
                "可执行": False,
                "理由": sell_sig.reason,
            }
        ]
        holdings = [
            {"股票代码": "600498", "股票名称": "烽火通信", "买入价": 80.0, "持仓股数": 500},
            {"股票代码": "002436", "股票名称": "兴森科技", "买入价": 47.7, "持仓股数": 100},
        ]
        with patch("quant.store.state.get_holdings", return_value=holdings):
            text = build_during_market_push(
                ctx.payload,
                timestamp="2026-06-18 14:37:00",
                raw_sell=[sell_sig],
                audit=audit,
                ctx=ctx,
            )
        self.assertIn("卖信号·确认中：烽火通信 500股", text)
        self.assertIn("1/2次", text)
        self.assertNotIn("·未卖 烽火通信", text)
        self.assertNotIn("·未卖", text)
        self.assertLess(text.index("卖信号·确认中"), text.index("💼 持仓"))

    def test_no_signal_shows_skip_reasons(self) -> None:
        ctx = ScoreContext.from_payload(
            {
                "自选股": [
                    {
                        "股票代码": "300750",
                        "股票名称": "宁德时代",
                        "盘口": {"最新": 200.0, "涨幅": 3.8},
                    }
                ],
                "持仓股": [],
            },
            mode="during_market",
        )
        with patch("quant.store.state.get_holdings", return_value=[]):
            text = build_during_market_push(
                ctx.payload,
                timestamp="2026-06-18 14:35:00",
                ctx=ctx,
            )
        self.assertIn("暂无信号", text)
        self.assertIn("·未买", text)
        self.assertIn("宁德时代", text)

    def test_no_signal_shows_sell_before_buy(self) -> None:
        ctx = ScoreContext.from_payload(
            {
                "自选股": [],
                "持仓股": [
                    {
                        "股票代码": "002436",
                        "股票名称": "兴森科技",
                        "买入价": 47.7,
                        "持仓股数": 100,
                        "盘口": {"最新": 47.7, "涨幅": 0.0},
                    }
                ],
            },
            mode="during_market",
        )
        holdings = [{"股票代码": "002436", "股票名称": "兴森科技", "买入价": 47.7, "持仓股数": 100}]
        with patch("quant.store.state.get_holdings", return_value=holdings):
            text = build_during_market_push(
                ctx.payload,
                timestamp="2026-06-18 14:35:00",
                ctx=ctx,
            )
        self.assertIn("·未卖", text)
        self.assertIn("兴森科技", text)
        self.assertLess(text.index("·未卖"), text.index("·未买") if "·未买" in text else len(text))

    def test_holding_shows_qty_and_position(self) -> None:
        payload = {
            "自选股": [],
            "持仓股": [
                {
                    "股票代码": "002436",
                    "股票名称": "兴森科技",
                    "买入价": 47.7,
                    "持仓股数": 100,
                    "盘口": {"最新": 47.7, "涨幅": 0.0},
                }
            ],
        }
        holdings = [{"股票代码": "002436", "股票名称": "兴森科技", "买入价": 47.7, "持仓股数": 100}]
        account = {"可用资金": 90000.0, "持仓市值": 4770.0, "总资产": 94770.0}
        with patch("quant.store.state.get_holdings", return_value=holdings):
            with patch("quant.store.state.get_account", return_value=account):
                text = build_during_market_push(
                    payload,
                    timestamp="2026-06-18 14:35:00",
                )
        self.assertIn("100股", text)
        self.assertIn("仓", text)
        self.assertIn("47.70→47.70", text)
        self.assertIn("总仓", text)

    def test_board_brief_shows_outflow_minus(self) -> None:
        payload = {
            "行业板块": {
                "资金流出榜": [
                    {"行业": "电力", "净额": 93.5, "涨跌幅": -3.52},
                ],
                "涨幅榜": [{"行业": "半导体", "涨跌幅": 1.8}],
            },
            "概念板块": {
                "资金流入榜": [
                    {"行业": "5G", "净额": 133.7, "涨跌幅": 0.72},
                ],
            },
            "自选股": [],
            "持仓股": [],
        }
        with patch("quant.store.state.get_holdings", return_value=[]):
            text = build_during_market_push(
                payload,
                timestamp="2026-06-18 14:35:00",
            )
        self.assertIn("流出：电力-93.5亿", text)
        self.assertIn("5G+133", text)


if __name__ == "__main__":
    unittest.main()
