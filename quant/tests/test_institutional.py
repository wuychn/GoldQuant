"""机构模块单元测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from quant.io.quotes import build_stock_by_code
from quant.narrative.during_market_push import build_during_market_push
from quant.narrative.push_sanitize import sanitize_feishu_body
from quant.portfolio.sizer import compute_buy_quantity
from quant.push.format import format_push_message
from quant.risk.manager import RiskManager
from quant.scoring.context import ScoreContext


class TestQuotesMerge(unittest.TestCase):
    def test_holding_does_not_wipe_quote(self) -> None:
        payload = {
            "自选股": [
                {
                    "股票代码": "300706",
                    "股票名称": "阿石创",
                    "盘口": {"最新": 80.5},
                }
            ],
            "持仓股": [
                {
                    "股票代码": "300706",
                    "股票名称": "阿石创",
                    "买入价": 78.0,
                    "持仓股数": 600,
                }
            ],
        }
        m = build_stock_by_code(payload)
        self.assertEqual(m["300706"]["持仓股数"], 600)
        self.assertEqual(m["300706"]["盘口"]["最新"], 80.5)


class TestPush(unittest.TestCase):
    def test_format_and_sanitize(self) -> None:
        msg = format_push_message("智能盯盘", "2026-07-23 10:00", "测试\n\n正文")
        self.assertIn("【智能盯盘】", msg)
        clean = sanitize_feishu_body("a\n\n\nb")
        self.assertEqual(clean, "a\n\nb")

    def test_during_push_minimal(self) -> None:
        text = build_during_market_push(
            {"持仓股": [], "自选股": []},
            timestamp="2026-07-23 10:00",
        )
        self.assertIn("智能盯盘", text)


class TestRisk(unittest.TestCase):
    @patch("quant.risk.manager.get_total_assets", return_value=500_000)
    @patch("quant.risk.manager.get_cash", return_value=400_000)
    @patch("quant.risk.manager.get_holdings", return_value=[])
    @patch("quant.risk.manager.compute_holdings_market_value", return_value=0)
    def test_buy_allowed(self, *_m) -> None:
        rm = RiskManager({"enabled": True, "max_single_name_pct": 0.25})
        ctx = ScoreContext.from_payload({}, mode="during_market")
        ok, _ = rm.check_buy("300706", sector_tags=["电子"], price=50.0, quantity=100, ctx=ctx)
        self.assertTrue(ok)


class TestPortfolioSizer(unittest.TestCase):
    @patch("quant.portfolio.sizer.get_total_assets", return_value=500_000)
    @patch("quant.portfolio.sizer.active_holding_count", return_value=0)
    @patch("quant.portfolio.sizer.allocate_buy_quantities_by_score", return_value={"300706": 200})
    @patch("quant.portfolio.sizer.position_limits", return_value={"max_stocks": 3, "total_pct": 50})
    def test_qty_positive(self, *_m) -> None:
        ctx = ScoreContext.from_payload({}, mode="during_market")
        stock = {"股票代码": "300706", "股票名称": "测试"}
        qty = compute_buy_quantity(stock, ctx, 50.0, alpha_score=75)
        self.assertGreaterEqual(qty, 100)


if __name__ == "__main__":
    unittest.main()
