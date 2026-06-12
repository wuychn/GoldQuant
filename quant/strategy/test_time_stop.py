"""时间/横盘止损单元测试。"""

from __future__ import annotations

import unittest
from datetime import date

from quant.strategy.time_stop import time_stop_triggers_sell, trading_days_since_buy


def _hist(days: list[tuple[str, float]]) -> list[dict]:
    return [{"日期": d, "收盘": c, "涨跌幅": 0.1} for d, c in days]


class TimeStopTests(unittest.TestCase):
    def test_not_enough_hold_days(self) -> None:
        stock = {
            "买入时间": "2026-06-10",
            "历史行情": _hist(
                [
                    ("2026-06-09", 10.0),
                    ("2026-06-10", 10.1),
                    ("2026-06-11", 10.05),
                ]
            ),
        }
        ok, _ = time_stop_triggers_sell(
            stock,
            {},
            pnl_pct=1.0,
            buy_date=date(2026, 6, 10),
        )
        self.assertFalse(ok)

    def test_exempt_when_gain_too_high(self) -> None:
        stock = {
            "历史行情": _hist(
                [(f"2026-06-{d:02d}", 10.0 + d * 0.1) for d in range(1, 13)]
            ),
        }
        ok, _ = time_stop_triggers_sell(
            stock,
            {},
            pnl_pct=6.0,
            buy_date=date(2026, 6, 1),
        )
        self.assertFalse(ok)

    def test_triggers_on_sideways_stuck(self) -> None:
        base = 10.0
        days = []
        for i in range(12):
            # 买入后 6 根 K，窄幅在 10.0~10.2
            c = base + (0.2 if i % 2 else 0.0)
            days.append((f"2026-06-{i + 1:02d}", c))
        stock = {"历史行情": _hist(days)}
        ok, reason = time_stop_triggers_sell(
            stock,
            {},
            pnl_pct=1.5,
            buy_date=date(2026, 6, 5),
        )
        self.assertTrue(ok)
        self.assertIn("持", reason)

    def test_trading_days_since_buy(self) -> None:
        stock = {
            "历史行情": _hist(
                [
                    ("2026-06-08", 10.0),
                    ("2026-06-09", 10.0),
                    ("2026-06-10", 10.0),
                    ("2026-06-11", 10.0),
                ]
            ),
        }
        self.assertEqual(trading_days_since_buy(stock, date(2026, 6, 9)), 2)


if __name__ == "__main__":
    unittest.main()
