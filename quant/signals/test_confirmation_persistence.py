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


class PersistenceSatisfiedTests(unittest.TestCase):
    def test_zero_persistence_immediate(self) -> None:
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
            persistence_satisfied(entry, _ts(9, 37), persistence_minutes=0, min_consecutive_runs=1)
        )

    def test_requires_time_and_runs(self) -> None:
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
            persistence_satisfied(entry, _ts(9, 47), persistence_minutes=20, min_consecutive_runs=3)
        )
        entry.count = 3
        self.assertFalse(
            persistence_satisfied(entry, _ts(9, 47), persistence_minutes=20, min_consecutive_runs=3)
        )
        self.assertTrue(
            persistence_satisfied(entry, _ts(9, 57), persistence_minutes=20, min_consecutive_runs=3)
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

    def test_condition_break_clears_pending(self) -> None:
        from unittest.mock import patch

        from quant.scoring.context import ScoreContext
        from quant.signals import confirmation as mod

        pending: dict[str, PendingSignal] = {}
        entry = PendingSignal(
            code="600226",
            action="买入",
            signal_kind="上升途中",
            count=2,
            first_at=_ts(9, 37).isoformat(),
            last_at=_ts(9, 47).isoformat(),
            regime="震荡",
        )
        pending[entry.key()] = entry

        conf = {
            "persistence_minutes": 20.0,
            "min_consecutive_runs": 3,
            "max_window_minutes": 180.0,
            "regime": "震荡",
        }

        with (
            patch.object(mod, "load_pending", return_value=dict(pending)),
            patch.object(mod, "save_pending") as save_mock,
            patch.object(mod, "_clear_opposite_pending"),
            patch.object(mod, "_now", return_value=_ts(9, 57)),
            patch.object(mod, "confirmation_config", return_value=conf),
        ):
            ctx = ScoreContext.from_payload({}, mode="during_market")
            executable, audit = mod.apply_three_confirmations([], ctx, scope_action="买入")
            self.assertEqual(executable, [])
            save_mock.assert_called_once()
            saved = save_mock.call_args[0][0]
            self.assertEqual(saved, {})

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
        )
        pending = {sell_entry.key(): sell_entry}

        with (
            patch.object(mod, "load_pending", return_value=dict(pending)),
            patch.object(mod, "save_pending") as save_mock,
            patch.object(mod, "_clear_opposite_pending"),
            patch.object(mod, "_now", return_value=_ts(9, 47)),
        ):
            ctx = ScoreContext.from_payload({}, mode="during_market")
            mod.apply_three_confirmations([], ctx, scope_action="买入")
            saved = save_mock.call_args[0][0]
            self.assertIn(sell_entry.key(), saved)

    def test_stop_loss_executes_first_run(self) -> None:
        from unittest.mock import patch

        from quant.scoring.context import ScoreContext
        from quant.signals import confirmation as mod

        conf = {
            "persistence_minutes": 0.0,
            "min_consecutive_runs": 1,
            "max_window_minutes": 180.0,
            "regime": "震荡",
        }

        with (
            patch.object(mod, "load_pending", return_value={}),
            patch.object(mod, "save_pending"),
            patch.object(mod, "_clear_opposite_pending"),
            patch.object(mod, "_now", return_value=_ts(10, 0)),
            patch.object(mod, "confirmation_config", return_value=conf),
        ):
            ctx = ScoreContext.from_payload({}, mode="during_market")
            executable, _ = mod.apply_three_confirmations([self._sig()], ctx)
            self.assertEqual(len(executable), 1)
            self.assertIn("持续确认完成", executable[0].reason)


if __name__ == "__main__":
    unittest.main()
