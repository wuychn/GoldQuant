"""持续确认逻辑单元测试（不依赖外部配置与持仓）。"""

from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from quant.signals.confirmation import PendingSignal, persistence_satisfied
from quant.signals.models import TradeSignal

_TZ = ZoneInfo("Asia/Shanghai")


def _ts(h: int, m: int) -> datetime:
    return datetime(2026, 6, 11, h, m, 0, tzinfo=_TZ)


def _day_latch_conf(**overrides) -> dict:
    base = {
        "min_span_minutes": 10.0,
        "min_day_hits": 2,
        "persistence_minutes": 10.0,
        "min_consecutive_runs": 2,
        "max_window_minutes": 180.0,
        "regime": "震荡",
        "day_latch": True,
        "same_day_window": True,
        "purge_on_miss": False,
        "max_miss_streak": 0,
        "use_trading_minutes": False,
        "verify_before_execute": False,
    }
    base.update(overrides)
    return base


class PersistenceSatisfiedTests(unittest.TestCase):
    def test_zero_span_immediate_on_hits(self) -> None:
        entry = PendingSignal(
            code="600226",
            action="卖出",
            signal_kind="止损",
            count=1,
            first_at=_ts(9, 37).isoformat(),
            last_at=_ts(9, 37).isoformat(),
            regime="震荡",
        )
        self.assertTrue(
            persistence_satisfied(
                entry,
                _ts(9, 37),
                min_span_minutes=0,
                min_day_hits=1,
            )
        )

    def test_requires_span_and_hits(self) -> None:
        entry = PendingSignal(
            code="000001",
            action="买入",
            signal_kind="上升途中",
            count=2,
            first_at=_ts(9, 37).isoformat(),
            last_at=_ts(9, 47).isoformat(),
            regime="震荡",
        )
        self.assertFalse(
            persistence_satisfied(entry, _ts(9, 47), min_span_minutes=20, min_day_hits=2)
        )
        self.assertTrue(
            persistence_satisfied(entry, _ts(9, 57), min_span_minutes=20, min_day_hits=2)
        )


class ApplyPersistenceFlowTests(unittest.TestCase):
    def _sig(self, code: str = "600226", action: str = "卖出", kind: str = "止损") -> TradeSignal:
        return TradeSignal(
            action=action,
            code=code,
            name="测试",
            price=10.0,
            quantity=100,
            strategy="主升浪战法",
            reason="测试",
            signal_kind=kind,
        )

    def test_sell_miss_keeps_daily_latch(self) -> None:
        from unittest.mock import patch

        from quant.scoring.context import ScoreContext
        from quant.signals import confirmation as mod

        entry = PendingSignal(
            code="600498",
            action="卖出",
            signal_kind="日内走弱",
            count=1,
            first_at=_ts(14, 37).isoformat(),
            last_at=_ts(14, 37).isoformat(),
            regime="震荡",
            first_date="2026-06-11",
            name="烽火通信",
        )
        pending = {entry.key(): entry}

        with (
            patch.object(mod, "load_pending", return_value=dict(pending)),
            patch.object(mod, "save_pending") as save_mock,
            patch.object(mod, "_clear_opposite_pending"),
            patch.object(mod, "_now", return_value=_ts(14, 47)),
            patch.object(mod, "cn_date_str", return_value="2026-06-11"),
            patch.object(mod, "resolve_payload_holdings", return_value=[{"股票代码": "600498"}]),
            patch.object(mod, "confirmation_config", return_value=_day_latch_conf()),
        ):
            ctx = ScoreContext.from_payload({}, mode="during_market")
            executable, audit = mod.apply_three_confirmations([], ctx, scope_action="卖出")
            self.assertEqual(executable, [])
            saved = save_mock.call_args[0][0]
            self.assertIn(entry.key(), saved)
            self.assertEqual(saved[entry.key()].count, 1)
            self.assertEqual(saved[entry.key()].miss_streak, 1)
            self.assertTrue(any("锁存" in str(r.get("状态", "")) for r in audit))

    def test_sold_out_code_purges_sell_pending(self) -> None:
        from unittest.mock import patch

        from quant.scoring.context import ScoreContext
        from quant.signals import confirmation as mod

        entry = PendingSignal(
            code="600498",
            action="卖出",
            signal_kind="日内走弱",
            count=1,
            first_at=_ts(14, 37).isoformat(),
            last_at=_ts(14, 37).isoformat(),
            regime="震荡",
            first_date="2026-06-11",
            name="烽火通信",
        )
        pending = {entry.key(): entry}

        with (
            patch.object(mod, "load_pending", return_value=dict(pending)),
            patch.object(mod, "save_pending") as save_mock,
            patch.object(mod, "_clear_opposite_pending"),
            patch.object(mod, "_now", return_value=_ts(14, 47)),
            patch.object(mod, "cn_date_str", return_value="2026-06-11"),
            patch.object(mod, "resolve_payload_holdings", return_value=[]),
            patch.object(mod, "confirmation_config", return_value=_day_latch_conf()),
        ):
            ctx = ScoreContext.from_payload({}, mode="during_market")
            executable, audit = mod.apply_three_confirmations([], ctx, scope_action="卖出")
            self.assertEqual(executable, [])
            saved = save_mock.call_args[0][0]
            self.assertNotIn(entry.key(), saved)
            self.assertFalse(audit)

    def test_sell_second_hit_executes_after_span(self) -> None:
        from unittest.mock import patch

        from quant.scoring.context import ScoreContext
        from quant.signals import confirmation as mod

        entry = PendingSignal(
            code="600498",
            action="卖出",
            signal_kind="日内走弱",
            count=1,
            first_at=_ts(14, 37).isoformat(),
            last_at=_ts(14, 37).isoformat(),
            regime="震荡",
            first_date="2026-06-11",
            name="烽火通信",
        )
        pending = {entry.key(): entry}
        sig = self._sig(code="600498", action="卖出", kind="日内走弱")

        with (
            patch.object(mod, "load_pending", return_value=dict(pending)),
            patch.object(mod, "save_pending"),
            patch.object(mod, "_clear_opposite_pending"),
            patch.object(mod, "_now", return_value=_ts(14, 47)),
            patch.object(mod, "cn_date_str", return_value="2026-06-11"),
            patch.object(mod, "resolve_payload_holdings", return_value=[{"股票代码": "600498"}]),
            patch.object(mod, "confirmation_config", return_value=_day_latch_conf(verify_before_execute=True)),
            patch("quant.signals.sell.verify_sell_signal_still_valid", return_value=True),
        ):
            ctx = ScoreContext.from_payload({}, mode="during_market")
            executable, _ = mod.apply_three_confirmations([sig], ctx, scope_action="卖出")
            self.assertEqual(len(executable), 1)
            self.assertIn("累计确认完成", executable[0].reason)

    def test_empty_buy_does_not_clear_sell_pending(self) -> None:
        from unittest.mock import patch

        from quant.scoring.context import ScoreContext
        from quant.signals import confirmation as mod

        sell_entry = PendingSignal(
            code="600226",
            action="卖出",
            signal_kind="日内走弱",
            count=1,
            first_at=_ts(9, 37).isoformat(),
            last_at=_ts(9, 37).isoformat(),
            regime="震荡",
            first_date="2026-06-11",
        )
        pending = {sell_entry.key(): sell_entry}

        with (
            patch.object(mod, "load_pending", return_value=dict(pending)),
            patch.object(mod, "save_pending") as save_mock,
            patch.object(mod, "_clear_opposite_pending"),
            patch.object(mod, "_now", return_value=_ts(9, 47)),
            patch.object(mod, "cn_date_str", return_value="2026-06-11"),
            patch.object(mod, "resolve_payload_holdings", return_value=[{"股票代码": "600226"}]),
            patch.object(mod, "confirmation_config", return_value=_day_latch_conf()),
        ):
            ctx = ScoreContext.from_payload({}, mode="during_market")
            mod.apply_three_confirmations([], ctx, scope_action="买入")
            saved = save_mock.call_args[0][0]
            self.assertIn(sell_entry.key(), saved)

    def test_stop_loss_waits_before_1430(self) -> None:
        from unittest.mock import patch

        from quant.scoring.context import ScoreContext
        from quant.signals import confirmation as mod

        conf = _day_latch_conf(min_span_minutes=0.0, min_day_hits=1)

        with (
            patch.object(mod, "load_pending", return_value={}),
            patch.object(mod, "save_pending"),
            patch.object(mod, "_clear_opposite_pending"),
            patch.object(mod, "_now", return_value=_ts(10, 0)),
            patch.object(mod, "confirmation_config", return_value=conf),
        ):
            ctx = ScoreContext.from_payload({}, mode="during_market")
            executable, audit = mod.apply_three_confirmations([self._sig()], ctx)
            self.assertEqual(len(executable), 0)
            self.assertTrue(any("14:30" in str(r.get("状态", "")) for r in audit))

    def test_stop_loss_executes_after_1430(self) -> None:
        from unittest.mock import patch

        from quant.scoring.context import ScoreContext
        from quant.signals import confirmation as mod

        conf = _day_latch_conf(min_span_minutes=0.0, min_day_hits=1)

        with (
            patch.object(mod, "load_pending", return_value={}),
            patch.object(mod, "save_pending"),
            patch.object(mod, "_clear_opposite_pending"),
            patch.object(mod, "_now", return_value=_ts(14, 35)),
            patch.object(mod, "confirmation_config", return_value=conf),
        ):
            ctx = ScoreContext.from_payload({}, mode="during_market")
            executable, _ = mod.apply_three_confirmations([self._sig()], ctx)
            self.assertEqual(len(executable), 1)
            self.assertIn("累计确认完成", executable[0].reason)


if __name__ == "__main__":
    unittest.main()
