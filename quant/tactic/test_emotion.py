import unittest

from quant.tactic.emotion import evaluate_emotion


class EmotionTests(unittest.TestCase):
    def test_green(self):
        e = evaluate_emotion(
            dt_n=1, zt_n=40, zb_n=5, n_2board=10, n_2to3=5, y_zt_prem=4.0
        )
        self.assertEqual(e.new_entry, "open")
        self.assertEqual(e.lights["dt"], "green")

    def test_dt_red(self):
        e = evaluate_emotion(
            dt_n=20, zt_n=10, zb_n=2, n_2board=10, n_2to3=5, y_zt_prem=4.0
        )
        self.assertEqual(e.new_entry, "block")
        self.assertEqual(e.lights["dt"], "red")

    def test_zb_yellow_plan_only(self):
        e = evaluate_emotion(
            dt_n=1, zt_n=10, zb_n=5, n_2board=10, n_2to3=5, y_zt_prem=4.0
        )
        self.assertEqual(e.zb_rate, 5 / 15)
        self.assertEqual(e.new_entry, "plan_only")

    def test_promo_red(self):
        e = evaluate_emotion(
            dt_n=0, zt_n=40, zb_n=5, n_2board=10, n_2to3=1, y_zt_prem=5.0
        )
        self.assertEqual(e.new_entry, "block")

    def test_prem_red(self):
        e = evaluate_emotion(
            dt_n=0, zt_n=40, zb_n=5, n_2board=10, n_2to3=5, y_zt_prem=-0.2
        )
        self.assertEqual(e.new_entry, "block")

    def test_high_dt_tightens(self):
        e = evaluate_emotion(
            dt_n=1, zt_n=40, zb_n=5, n_2board=10, n_2to3=5, y_zt_prem=4.0, high_dt_n=3
        )
        self.assertEqual(e.new_entry, "plan_only")

    def test_dt_3_not_safe(self):
        e = evaluate_emotion(
            dt_n=3, zt_n=40, zb_n=5, n_2board=10, n_2to3=5, y_zt_prem=4.0
        )
        self.assertEqual(e.lights["dt"], "yellow")
        self.assertEqual(e.new_entry, "plan_only")

    def test_promo_light_weight(self):
        e = evaluate_emotion(
            dt_n=1, zt_n=40, zb_n=5, n_2board=10, n_2to3=3, y_zt_prem=4.0
        )
        self.assertEqual(e.lights["promo"], "yellow")
        self.assertEqual(e.new_entry, "plan_only")
        self.assertEqual(e.weight_scale, 0.5)
