import unittest

from quant.tactic.bars import (
    Bar,
    a_kill,
    consecutive_close_limit_down,
    last_zt_streak,
    natural_break_index,
)
from quant.tactic.engine import rank_watch
from quant.tactic.mode_a import evaluate_mode_a
from quant.tactic.mode_b import evaluate_mode_b


def _bar(day: str, o, h, lo, c, prev, vol=1e6) -> Bar:
    return Bar(date=day, open=o, high=h, low=lo, close=c, volume=vol, prev_close=prev, name="测")


def _zt_seq(n: int, start_prev=10.0) -> list[Bar]:
    bars = []
    prev = start_prev
    for i in range(n):
        up = round(prev * 1.1, 2)
        bars.append(_bar(f"2024-01-{i+1:02d}", up, up, up, up, prev, vol=2e6))
        prev = up
    return bars


class RankWatchTests(unittest.TestCase):
    def test_cap_three_buy_first(self):
        rows = [
            {"code": "1", "mode": "observe", "buy": False, "boards": 9},
            {"code": "2", "mode": "B", "buy": True, "boards": 5},
            {"code": "3", "mode": "A", "buy": True, "boards": 6},
            {"code": "4", "mode": "B", "buy": True, "boards": 7},
        ]
        out = rank_watch(rows)
        self.assertEqual([x["code"] for x in out], ["3", "4", "2"])


class StreakTests(unittest.TestCase):
    def test_five_board_then_break(self):
        bars = _zt_seq(5)
        last = bars[-1]
        bars.append(_bar("2024-01-06", last.close * 0.98, last.close * 1.01, last.close * 0.97, last.close * 0.99, last.close, vol=5e5))
        n, _, end = last_zt_streak(bars, "600000")
        self.assertEqual(n, 5)
        self.assertEqual(end, 4)
        self.assertIsNotNone(natural_break_index(bars, "600000"))


class WindowScanTests(unittest.TestCase):
    def test_two_dt_not_only_tail(self):
        bars = _zt_seq(5)
        px = bars[-1].close
        for i in range(2):
            d = round(px * 0.9, 2)
            bars.append(_bar(f"2024-01-{6+i:02d}", d, px * 0.95, d, d, px, vol=4e5))
            px = d
        for i in range(3):
            bars.append(_bar(f"2024-01-{8+i:02d}", px, px * 1.01, px * 0.99, px * 1.005, px, vol=3e5))
        self.assertTrue(consecutive_close_limit_down(bars[5:], "600000"))

    def test_a_kill_not_only_tail(self):
        bars = _zt_seq(5)
        px = bars[-1].close
        c = round(px * 0.91, 2)
        bars.append(_bar("2024-01-06", px, px, c, c, px, vol=4e5))
        lo = c * 0.97
        bars.append(_bar("2024-01-07", c, c, lo, c * 1.01, c, vol=4e5))
        for i in range(3):
            bars.append(_bar(f"2024-01-{8+i:02d}", c, c * 1.02, c * 0.99, c * 1.01, c, vol=3e5))
        self.assertTrue(a_kill(bars[5:]))


class ModeBTests(unittest.TestCase):
    def test_buy_after_3_chop_days(self):
        bars = _zt_seq(5)
        px = bars[-1].close
        for i in range(4):
            bars.append(
                _bar(
                    f"2024-01-{6+i:02d}",
                    px * 0.99,
                    px * 1.01,
                    px * 0.98,
                    px * 0.995,
                    px,
                    vol=3e5,
                )
            )
        out = evaluate_mode_b(bars, "600000")
        self.assertTrue(out["ok"])
        self.assertTrue(out["buy"])

    def test_close_limit_down_counts_as_break(self):
        bars = _zt_seq(5)
        px = bars[-1].close
        down = round(px * 0.9, 2)
        bars.append(_bar("2024-01-06", down, down, down, down, px))
        self.assertIsNotNone(natural_break_index(bars, "600000"))

    def test_two_close_dt_deletes(self):
        bars = _zt_seq(5)
        px = bars[-1].close
        for i in range(2):
            d = round(px * 0.9, 2)
            bars.append(_bar(f"2024-01-{6+i:02d}", d, d, d, d, px, vol=4e5))
            px = d
        out = evaluate_mode_b(bars, "600000")
        self.assertTrue(out["fail"])

    def test_10d_no_launch_deletes(self):
        bars = _zt_seq(5)
        px = bars[-1].close
        for i in range(12):
            day = 6 + i
            bars.append(
                _bar(
                    f"2024-01-{day:02d}" if day <= 31 else f"2024-02-{day-31:02d}",
                    px * 0.99,
                    px * 0.995,
                    px * 0.98,
                    px * 0.985,
                    px,
                    vol=3e5,
                )
            )
        out = evaluate_mode_b(bars, "600000")
        self.assertTrue(out["fail"])
        self.assertTrue(any("10日" in r or "启动" in r for r in out["reasons"]))

    def test_volume_yang_counts_as_launch(self):
        bars = _zt_seq(5)
        px = bars[-1].close
        for i in range(12):
            day = 6 + i
            ds = f"2024-01-{day:02d}" if day <= 31 else f"2024-02-{day-31:02d}"
            if i == 4:
                bars.append(_bar(ds, px * 0.99, px * 1.02, px * 0.98, px * 1.01, px, vol=4e6))
            else:
                bars.append(_bar(ds, px * 0.99, px * 0.995, px * 0.98, px * 0.985, px, vol=3e5))
        out = evaluate_mode_b(bars, "600000")
        self.assertFalse(out["fail"])

    def test_one_word_limit_down_is_not_deleted(self):
        bars = _zt_seq(5)
        px = bars[-1].close
        down = round(px * 0.9, 2)
        bars.append(_bar("2024-01-06", down, down, down, down, px))
        out = evaluate_mode_b(bars, "600000")
        self.assertFalse(out["fail"])
        self.assertFalse(out["buy"])

    def test_chop_drawdown_deletes(self):
        bars = _zt_seq(5)
        px = bars[-1].close
        bars.append(_bar("2024-01-06", px * 0.98, px, px * 0.80, px * 0.97, px, vol=4e5))
        bars.append(_bar("2024-01-07", px * 0.90, px * 0.90, px * 0.82, px * 0.82, px * 0.97, vol=3e5))
        main = evaluate_mode_b(bars, "600000")
        self.assertTrue(main["fail"])
        self.assertTrue(any("横盘回撤" in r for r in main["reasons"]))
        growth = evaluate_mode_b(bars, "300001")
        self.assertFalse(any("横盘回撤" in r for r in growth["reasons"]))


class ModeATests(unittest.TestCase):
    def test_hard_fail_two_limit_down(self):
        bars = _zt_seq(5)
        px = bars[-1].close
        for i in range(2):
            d = round(px * 0.9, 2)
            bars.append(_bar(f"2024-01-{6+i:02d}", d, px * 0.95, d, d, px, vol=4e5))
            px = d
        out = evaluate_mode_a(bars, "600000", yidong_i=2)
        self.assertTrue(out["fail"])
        self.assertFalse(out["buy"])

    def test_a_kill_mid_window_deletes(self):
        bars = _zt_seq(5)
        px = bars[-1].close
        c = round(px * 0.91, 2)
        bars.append(_bar("2024-01-06", px, px, c, c, px, vol=4e5))
        lo = c * 0.97
        bars.append(_bar("2024-01-07", c, c, lo, c * 1.01, c, vol=4e5))
        for i in range(4):
            bars.append(_bar(f"2024-01-{8+i:02d}", c, c * 1.02, c * 0.99, c * 1.01, c, vol=3e5))
        out = evaluate_mode_a(bars, "600000", yidong_i=4)
        self.assertTrue(out["fail"])
        self.assertTrue(any("A杀" in r for r in out["reasons"]))

    def test_rebound_high_not_required(self):
        pad = []
        px0 = 8.0
        for i in range(20):
            pad.append(_bar(f"2023-12-{i+1:02d}", px0, px0 * 1.01, px0 * 0.99, px0, px0, vol=1e6))
        bars = pad + _zt_seq(5)
        px = bars[-1].close
        for i in range(8):
            bars.append(
                _bar(
                    f"2024-01-{6+i:02d}",
                    px * 0.99,
                    px * 1.0,
                    px * 0.97,
                    px * 0.995,
                    px,
                    vol=3e5,
                )
            )
        out = evaluate_mode_a(bars, "600000", yidong_i=len(pad) + 4)
        self.assertFalse(out["fail"])

    def test_chinext_deletes_only_above_30(self):
        bars = _zt_seq(5)
        px = bars[-1].close
        mild = round(px * 0.72, 2)
        bars.append(_bar("2024-01-06", mild, px, mild, mild, px, vol=1e5))
        under = evaluate_mode_a(bars, "300001", yidong_i=4)
        self.assertFalse(any(r.startswith("调整") for r in under["reasons"]))
        deep = round(px * 0.68, 2)
        bars[-1] = _bar("2024-01-06", deep, px, deep, deep, px, vol=1e5)
        over = evaluate_mode_a(bars, "300001", yidong_i=4)
        self.assertTrue(over["fail"])
        self.assertTrue(any(r.startswith("调整") for r in over["reasons"]))


class FreshLimitUpTests(unittest.TestCase):
    def test_limit_up_after_break_is_not_chop(self):
        from quant.tactic.engine import _classify, _focus_reasons

        bars = _zt_seq(5)
        px = bars[-1].close
        bars.append(_bar("2024-01-06", px * 0.99, px * 1.01, px * 0.98, px * 0.995, px, vol=8e5))
        prev = bars[-1].close
        up = round(prev * 1.1, 2)
        bars.append(_bar("2024-01-07", up, up, prev, up, prev, vol=9e5))
        out = _classify(bars, "600000")
        self.assertEqual(out["mode"], "observe")
        self.assertFalse(out["buy"])
        self.assertTrue(any("涨停" in r for r in out["reasons"]))
        self.assertFalse(any("缩量" in r for r in out["reasons"]))

        shown = _focus_reasons(
            {
                "reasons": ["横盘未缩量到70%"],
                "severe_next_ret": 0.0486,
                "severe_next_why": "10日偏离+100%",
            }
        )
        self.assertIn("规避", shown[0])
        self.assertNotIn("缩量", "".join(shown))
