"""卖出紧急度与止损判定测试。"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from quant.signals.models import TradeSignal
from quant.signals.sell_policy import (
    approaching_limit_down,
    is_urgent_sell,
    near_limit_up,
    sell_requires_late_session,
    stop_loss_exempt,
    stop_loss_triggers_sell,
)


class SellPolicyTests(unittest.TestCase):
    def test_stop_loss_not_urgent_by_default(self) -> None:
        sig = TradeSignal(
            action="卖出",
            code="600176",
            name="中国巨石",
            price=10.0,
            quantity=100,
            strategy="主升浪",
            reason="止损",
            sell_type="止损",
            signal_kind="止损",
        )
        stock = {"盘口": {"涨幅": -4.0, "最新": 10.0}}
        self.assertFalse(is_urgent_sell(sig, stock, "600176"))
        self.assertTrue(sell_requires_late_session(sig, stock, "600176"))

    @patch("quant.signals.sell_policy.trend_broken_for_stop", return_value=False)
    def test_no_stop_on_pnl_alone_when_trend_intact(self, _brk) -> None:
        ctx = MagicMock()
        stock = {"盘口": {"涨幅": -2.0, "最新": 9.0}, "买入价": 10.0}
        ok, _ = stop_loss_triggers_sell(
            stock,
            "600176",
            pnl_pct=-6.0,
            ctx=ctx,
            mw_cfg={},
            price=9.0,
        )
        self.assertFalse(ok)

    @patch("quant.signals.sell_policy.trend_broken_for_stop", return_value=False)
    def test_no_stop_on_limit_up_day_even_if_pnl_negative(self, _brk) -> None:
        ctx = MagicMock()
        stock = {"盘口": {"涨幅": 9.8, "最新": 11.0}}
        self.assertTrue(stop_loss_exempt(stock, "600176"))
        ok, _ = stop_loss_triggers_sell(
            stock,
            "600176",
            pnl_pct=-6.0,
            ctx=ctx,
            mw_cfg={},
            price=11.0,
        )
        self.assertFalse(ok)

    @patch("quant.signals.sell_policy.trend_broken_for_stop", return_value=True)
    @patch("quant.signals.sell_policy.is_late_session_for_trend_sell", return_value=False)
    def test_no_stop_before_1430_on_morning_dip(self, _late, _brk) -> None:
        """早盘浮亏深、午后可能拉回的场景：14:30 前不产生止损。"""
        ctx = MagicMock()
        stock = {"盘口": {"涨幅": -5.5, "最新": 9.0}}
        ok, _ = stop_loss_triggers_sell(
            stock,
            "600176",
            pnl_pct=-6.0,
            ctx=ctx,
            mw_cfg={},
            price=9.0,
        )
        self.assertFalse(ok)

    @patch("quant.signals.sell_policy.trend_broken_for_stop", return_value=True)
    @patch("quant.signals.sell_policy.is_late_session_for_trend_sell", return_value=True)
    def test_stop_after_1430_when_still_weak(self, _late, _brk) -> None:
        ctx = MagicMock()
        stock = {"盘口": {"涨幅": -4.0, "最新": 9.0}}
        ok, _ = stop_loss_triggers_sell(
            stock,
            "600176",
            pnl_pct=-6.0,
            ctx=ctx,
            mw_cfg={},
            price=9.0,
        )
        self.assertTrue(ok)

    @patch("quant.signals.sell_policy.trend_broken_for_stop", return_value=True)
    @patch("quant.signals.sell_policy.is_late_session_for_trend_sell", return_value=True)
    def test_no_stop_after_1430_if_rallied_to_limit_up(self, _late, _brk) -> None:
        """14:30 后已拉涨停：即使成本浮亏也不止损。"""
        ctx = MagicMock()
        stock = {"盘口": {"涨幅": 9.8, "最新": 11.0}}
        ok, _ = stop_loss_triggers_sell(
            stock,
            "600176",
            pnl_pct=-2.0,
            ctx=ctx,
            mw_cfg={},
            price=11.0,
        )
        self.assertFalse(ok)

    @patch("quant.signals.sell_policy.trend_broken_for_stop", return_value=True)
    def test_stop_when_trend_broken_and_pnl_bad(self, _brk) -> None:
        ctx = MagicMock()
        stock = {"盘口": {"涨幅": -4.0, "最新": 9.0}}
        with patch(
            "quant.signals.sell_policy.is_late_session_for_trend_sell",
            return_value=True,
        ):
            ok, reason = stop_loss_triggers_sell(
                stock,
                "600176",
                pnl_pct=-6.0,
                ctx=ctx,
                mw_cfg={},
                price=9.0,
            )
        self.assertTrue(ok)
        self.assertIn("浮亏", reason)

    @patch("quant.signals.sell_policy.limit_pct", return_value=9.9)
    @patch("quant.signals.sell_policy.load_trade_sim_config")
    def test_near_limit_up(self, _cfg, _lim) -> None:
        self.assertTrue(near_limit_up({"盘口": {"涨幅": 9.6}}, "600176"))

    @patch("quant.signals.sell_policy.limit_pct", return_value=9.9)
    @patch("quant.signals.sell_policy.load_trade_sim_config")
    def test_approaching_limit_down(self, _cfg, _lim) -> None:
        self.assertTrue(approaching_limit_down({"盘口": {"涨幅": -9.5}}, "600176"))


if __name__ == "__main__":
    unittest.main()
