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

    def test_executed_sell_has_no_late_session_suffix(self) -> None:
        """已卖成交行不再追加「（14:30后）」进度后缀。"""
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
        audit = [
            {
                "股票代码": "600519",
                "股票名称": "贵州茅台",
                "方向": "卖出",
                "信号类型": "日内走弱",
                "状态": "累计确认完成（14:30后），可交易",
                "可执行": True,
            }
        ]
        with patch("quant.store.state.get_holdings", return_value=[]):
            text = build_during_market_push(
                {"自选股": [], "持仓股": []},
                timestamp="2026-06-18 14:40:00",
                raw_sell=[sell_sig],
                audit=audit,
                executed=[
                    ExecutedTrade(signal=sell_sig, timestamp="14:40:00", pnl=-120.0)
                ],
            )
        self.assertIn("已卖", text)
        self.assertNotIn("14:30", text)

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

    def test_pending_buy_only_in_audit_shows_confirming_line(self) -> None:
        payload = {
            "自选股": [
                {
                    "股票代码": "601689",
                    "股票名称": "拓普集团",
                    "盘口": {"最新": 67.10, "涨幅": 2.5},
                }
            ],
            "持仓股": [],
        }
        ctx = ScoreContext.from_payload(payload, mode="during_market")
        audit = [
            {
                "股票代码": "601689",
                "股票名称": "拓普集团",
                "方向": "买入",
                "信号类型": "上升途中",
                "确认次数": 1,
                "状态": "当日锁存确认中（累计1/2次，0/10分）",
                "可执行": False,
                "理由": "[上升途中]评分75",
            }
        ]
        with patch("quant.store.state.get_holdings", return_value=[]):
            text = build_during_market_push(
                payload,
                timestamp="2026-06-18 14:37:00",
                audit=audit,
                ctx=ctx,
            )
        self.assertIn("买信号·确认中：拓普集团", text)
        self.assertIn("1/2次", text)
        self.assertNotIn("·未买 拓普集团", text)

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

    def test_no_signal_summary_shows_total_not_sample_count(self) -> None:
        payload = {
            "自选股": [
                {"股票代码": f"30075{i}", "股票名称": f"自选{i}", "盘口": {"最新": 10.0}}
                for i in range(5)
            ],
            "持仓股": [
                {
                    "股票代码": f"60051{i}",
                    "股票名称": f"持仓{i}",
                    "买入价": 10.0,
                    "持仓股数": 100,
                    "盘口": {"最新": 10.0, "涨幅": 0.0},
                }
                for i in range(4)
            ],
        }
        ctx = ScoreContext.from_payload(payload, mode="during_market")
        buy_notes = ["自选0：强度还不够，再观察", "自选1：强度还不够，再观察"]
        sell_notes = ["持仓0：暂不减仓", "持仓1：暂不减仓"]
        holdings = payload["持仓股"]
        with patch("quant.store.state.get_holdings", return_value=holdings), patch(
            "quant.narrative.during_market_push.sample_no_trade_reasons",
            return_value=(buy_notes, sell_notes),
        ):
            text = build_during_market_push(
                payload,
                timestamp="2026-06-18 14:35:00",
                ctx=ctx,
            )
        self.assertIn("持仓共4只暂不减，节选2例", text)
        self.assertIn("自选共5只未达买点，节选2例", text)
        self.assertNotIn("2只持仓暂不减", text)
        self.assertNotIn("2只自选强度不够", text)

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

    def test_watchlist_anomaly_sorted_by_gain_with_industry_concept(self) -> None:
        payload = {
            "大盘指数": [],
            "概念板块": {
                "涨幅榜": [
                    {"行业": "存储芯片", "行业-涨跌幅": 2.1},
                    {"行业": "新能源", "行业-涨跌幅": 1.0},
                ],
            },
            "自选股": [
                {
                    "股票代码": "600001",
                    "股票名称": "低涨幅",
                    "行业": "半导体",
                    "所属概念": ["新能源", "存储芯片"],
                    "概念粘合度": [
                        {"rank": 1, "concept": "存储芯片"},
                        {"rank": 2, "concept": "新能源"},
                    ],
                    "盘口": {"涨幅": 1.0},
                },
                {
                    "股票代码": "600002",
                    "股票名称": "高涨幅",
                    "行业": "银行",
                    "所属概念": ["新能源"],
                    "盘口": {"涨幅": 5.0},
                },
            ],
            "持仓股": [],
        }
        with patch("quant.store.state.get_holdings", return_value=[]):
            text = build_during_market_push(
                payload,
                timestamp="2026-06-18 14:35:00",
            )
        self.assertLess(text.index("高涨幅"), text.index("低涨幅"))
        self.assertIn("银行行业", text)
        self.assertIn("存储芯片概念、新能源概念", text)
        self.assertIn("新能源概念", text)

    def test_watchlist_theme_concept_suffix_not_duplicated(self) -> None:
        payload = {
            "大盘指数": [],
            "概念板块": {},
            "自选股": [
                {
                    "股票代码": "300001",
                    "股票名称": "麦捷科技",
                    "行业": "元件",
                    "所属概念": ["芯片概念"],
                    "盘口": {"涨幅": 14.37},
                }
            ],
            "持仓股": [],
        }
        with patch("quant.store.state.get_holdings", return_value=[]):
            text = build_during_market_push(
                payload,
                timestamp="2026-06-18 14:35:00",
            )
        self.assertIn("元件行业 · 芯片概念", text)
        self.assertNotIn("概念芯片概念", text)

    def test_watchlist_anomaly_lists_all_by_gain(self) -> None:
        watchlist = [
            {
                "股票代码": f"60000{i}",
                "股票名称": f"股{i}",
                "盘口": {"涨幅": float(i)},
            }
            for i in range(25)
        ]
        payload = {
            "大盘指数": [],
            "概念板块": {},
            "自选股": watchlist,
            "持仓股": [],
        }
        with patch("quant.store.state.get_holdings", return_value=[]), patch(
            "quant.config.load_push_config",
            return_value={"watchlist_price_range": {"enabled": False}},
        ):
            text = build_during_market_push(
                payload,
                timestamp="2026-06-18 14:35:00",
            )
        self.assertIn("自选异动（25）", text)
        self.assertLess(text.index("股24"), text.index("股0"))
        for i in range(25):
            self.assertIn(f"股{i}", text)

    def test_watchlist_anomaly_dedups_by_code(self) -> None:
        """同代码重复行（历史 optional.jsonl 重复）应去重，仅展示一次。"""
        watchlist = [
            {"股票代码": "600584", "股票名称": "长电科技", "盘口": {"涨幅": 3.0, "最新": 30.0}},
            {"股票代码": "600584", "股票名称": "长电科技", "盘口": {"涨幅": 3.0, "最新": 30.0}},
            {"股票代码": "000636", "股票名称": "风华高科", "盘口": {"涨幅": 1.0, "最新": 20.0}},
        ]
        payload = {"大盘指数": [], "概念板块": {}, "自选股": watchlist, "持仓股": []}
        with patch("quant.store.state.get_holdings", return_value=[]), patch(
            "quant.config.load_push_config",
            return_value={"watchlist_price_range": {"enabled": False}},
        ):
            text = build_during_market_push(payload, timestamp="2026-06-18 14:35:00")
        self.assertIn("自选异动（2）", text)
        self.assertEqual(text.count("长电科技"), 1)

    def test_watchlist_anomaly_price_range_filters(self) -> None:
        """启用价格区间后，仅展示区间内自选，标题带区间标注。"""
        watchlist = [
            {"股票代码": "000001", "股票名称": "低价股", "盘口": {"涨幅": 2.0, "最新": 5.0}},
            {"股票代码": "000002", "股票名称": "中价股", "盘口": {"涨幅": 1.0, "最新": 25.0}},
            {"股票代码": "600519", "股票名称": "高价股", "盘口": {"涨幅": 3.0, "最新": 1500.0}},
        ]
        payload = {"大盘指数": [], "概念板块": {}, "自选股": watchlist, "持仓股": []}
        with patch(
            "quant.config.load_push_config",
            return_value={"watchlist_price_range": {"enabled": True, "min": 10, "max": 100}},
        ), patch("quant.store.state.get_holdings", return_value=[]):
            text = build_during_market_push(payload, timestamp="2026-06-18 14:35:00")
        self.assertIn("中价股", text)
        self.assertNotIn("低价股", text)
        self.assertNotIn("高价股", text)
        self.assertIn("10-100元", text)

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
