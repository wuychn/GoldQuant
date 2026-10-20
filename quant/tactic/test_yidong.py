import unittest

from quant.tactic.bars import Bar
from quant.tactic.yidong import (
    abnormal_threshold,
    first_yidong_index,
    guard_live,
    next_trigger_return,
    settle_for_next_session,
    severe_hit_count,
    severe_on_last,
)


def _b(day: str, close: float, prev: float, name: str = "") -> Bar:
    return Bar(
        date=day,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1e6,
        prev_close=prev,
        name=name,
    )


def _seq(rets: list[float], start_prev: float = 10.0) -> list[Bar]:
    bars = []
    prev = start_prev
    for i, r in enumerate(rets):
        c = round(prev * (1.0 + r), 4)
        bars.append(_b(f"2024-02-{i+1:02d}", c, prev))
        prev = c
    return bars


class YidongTests(unittest.TestCase):
    def test_plain_abnormal_is_not_severe(self):
        bars = _seq([0.10, 0.10, 0.10])
        self.assertIsNone(first_yidong_index(bars, "600000"))
        self.assertEqual(abnormal_threshold("600000", "*ST甲"), 0.12)

    def test_index_rise_delays_10d(self):
        bars = _seq([0.10] * 10)
        idx = {b.date: 0.02 for b in bars}
        self.assertIsNone(first_yidong_index(bars, "600000", index_ret=idx))

    def test_10d_cum_100pct_severe(self):
        bars = _seq([0.10] * 10)
        self.assertEqual(severe_hit_count("600000"), 4)
        self.assertEqual(first_yidong_index(bars, "600000"), 9)
        hit, why = severe_on_last(bars, "600000")
        self.assertTrue(hit)
        self.assertIn("10日偏离", why)

    def test_next_day_trigger_after_9_limit_ups(self):
        bars = _seq([0.10] * 9)
        ret, why = next_trigger_return(bars, "600000")
        self.assertIsNotNone(ret)
        self.assertAlmostEqual(ret, 0.10, places=2)
        self.assertIn("10日偏离", why)

    def test_limit_up_not_enough(self):
        bars = _seq([0.01] * 9)
        ret, _why = next_trigger_return(bars, "600000")
        self.assertIsNone(ret)

    def test_four_abnormal_hits_in_10d(self):
        bars = _seq([0.07] * 12)
        self.assertEqual(first_yidong_index(bars, "600000"), 11)

    def test_hit_keeps_mode_b_for_next_day(self):
        info = {"mode": "B", "buy": True, "fail": False, "reasons": ["可买"]}
        out = settle_for_next_session(info, hit_why="10日偏离+100%", halted=False)
        self.assertEqual(out["mode"], "B")
        self.assertTrue(out["buy"])
        self.assertFalse(out["fail"])
        self.assertIn("次日解除", out["reasons"][0])

    def test_halt_is_observe_not_drop(self):
        info = {"mode": "A", "buy": True, "fail": True, "reasons": ["A杀"]}
        out = settle_for_next_session(info, hit_why="", halted=True)
        self.assertEqual(out["mode"], "observe")
        self.assertFalse(out["buy"])
        self.assertFalse(out["fail"])

    def test_live_hit_observes(self):
        info = {"mode": "B", "buy": True, "fail": False, "reasons": []}
        out = guard_live(info, hit_why="10日偏离+100%", halted=False)
        self.assertEqual(out["mode"], "observe")
        self.assertFalse(out["buy"])
        self.assertFalse(out["fail"])
