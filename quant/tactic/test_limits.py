import unittest

from quant.tactic.limits import (
    board_limit_pct,
    chop_cap,
    close_limit_down,
    close_limit_up,
    is_fake_yin,
    is_long_lower_shadow,
    is_one_word_limit_down,
    is_stop_k,
    is_yang,
    pullback_cap,
)


class LimitTests(unittest.TestCase):
    def test_board_pct(self):
        self.assertEqual(board_limit_pct("600519"), 0.10)
        self.assertEqual(board_limit_pct("300001"), 0.20)
        self.assertEqual(board_limit_pct("688001"), 0.20)
        self.assertEqual(board_limit_pct("920006"), 0.30)
        self.assertEqual(board_limit_pct("000001", "*ST某某"), 0.05)

    def test_close_limit_up_main(self):
        self.assertTrue(close_limit_up(11.0, 10.0, "600000"))
        self.assertFalse(close_limit_up(10.5, 10.0, "600000"))

    def test_one_word_down(self):
        self.assertTrue(is_one_word_limit_down(9.0, 9.0, 9.0, 9.0, 10.0, "600000"))
        self.assertFalse(is_one_word_limit_down(9.5, 10.0, 9.0, 9.0, 10.0, "600000"))

    def test_stop_k(self):
        self.assertTrue(is_yang(10.0, 10.2))
        self.assertTrue(is_fake_yin(10.2, 10.1, 10.0))
        self.assertTrue(is_long_lower_shadow(10.0, 10.05, 9.5, 10.02))
        self.assertTrue(is_stop_k(10.0, 10.2, 9.9, 10.15, 10.0))
        self.assertFalse(is_stop_k(10.2, 10.2, 10.0, 10.0, 10.3))
        self.assertTrue(close_limit_down(9.0, 10.0, "600000"))

    def test_pullback_caps(self):
        self.assertEqual(pullback_cap("600000"), 0.20)
        self.assertEqual(pullback_cap("300001"), 0.30)
        self.assertEqual(chop_cap("600000"), 0.15)
        self.assertEqual(chop_cap("688001"), 0.20)
