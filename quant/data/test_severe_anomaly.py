"""东财严重异动列表：只解析源站字段，不本地重算临界涨幅。"""

from __future__ import annotations

import unittest

from quant.data.sources.eastmoney.severe_anomaly import (
    anomaly_type_key,
    parse_price_anomaly_payload,
)
from quant.tactic.format import severe_lines
from quant.tactic.yidong import pick_severe, stamp_em_severe


_PAYLOAD = {
    "result": 0,
    "data": [
        {"m": 0, "c": "002343", "n": "慈文传媒", "s": 1, "e": 4, "x": 90.81, "d": 8, "t": 4.86, "a": -0.1, "o": 0},
        {"m": 0, "c": "002343", "n": "慈文传媒", "s": 1, "e": 4, "x": 90.81, "d": 8, "t": 4.96, "a": -0.1, "o": 2},
        {"m": 0, "c": "300006", "n": "莱美药业", "s": 4, "e": 4, "x": 56.29, "d": 10, "t": 9.78, "a": -15.49, "o": 0},
        {"m": 0, "c": "920202", "n": "安达股份", "s": 6, "e": 5, "x": -46.96, "d": 10, "t": -24.01, "a": 4.13, "o": 0},
    ],
}


class SevereAnomalyParseTest(unittest.TestCase):
    def test_bj_type_remaps_to_minus_60(self):
        self.assertEqual(anomaly_type_key(6, 5), 50)
        self.assertEqual(anomaly_type_key(1, 4), 4)

    def test_parse_keeps_source_targets(self):
        rows = parse_price_anomaly_payload(_PAYLOAD)
        by = {(r["code"], r["session"]): r for r in rows}
        ciwen = by[("002343", "today")]
        self.assertAlmostEqual(ciwen["target_ret"], 0.0486)
        self.assertEqual(ciwen["days"], 8)
        self.assertEqual(ciwen["type_name"], "10日偏离+100%")
        self.assertFalse(ciwen["hit"])
        self.assertEqual(by[("002343", "next")]["target_ret"], 0.0496)
        anda = by[("920202", "today")]
        self.assertEqual(anda["type_name"], "10日偏离-60%")
        self.assertAlmostEqual(anda["target_ret"], -0.2401)
        self.assertFalse(anda["hit"])

    def test_hit_when_change_crosses_target(self):
        rows = parse_price_anomaly_payload(
            {"data": [{"c": "002343", "n": "慈文传媒", "s": 1, "e": 4, "x": 100.2, "d": 8, "t": 4.86, "a": 5.0, "o": 0}]}
        )
        self.assertTrue(rows[0]["hit"])

    def test_stamp_uses_next_card_and_skips_absent_names(self):
        rows = parse_price_anomaly_payload(_PAYLOAD)
        shown = pick_severe(rows, "002343", session="next")
        self.assertAlmostEqual(shown["target_ret"], 0.0496)
        stamped = stamp_em_severe({}, rows, "002343", session="next")
        self.assertAlmostEqual(stamped["severe_next_ret"], 0.0496)
        self.assertFalse(stamped["severe_today"])
        missing = stamp_em_severe({}, rows, "000001", session="today")
        self.assertIsNone(missing["severe_next_ret"])

    def test_copy_uses_source_threshold(self):
        rows = parse_price_anomaly_payload(_PAYLOAD)
        up = stamp_em_severe({"name": "慈文传媒", "code": "002343"}, rows, "002343", session="today")
        dn = stamp_em_severe({"name": "安达股份", "code": "920202"}, rows, "920202", session="today")
        text = "\n".join(severe_lines([up, dn], horizon="今日"))
        self.assertIn("达到+4.86%", text)
        self.assertIn("小于-24.01%", text)
        self.assertIn("当前8日偏离", text)
        self.assertNotIn("指数平盘", text)

    def test_other_sources_do_not_implement(self):
        from quant.data.sources.market.sina import SinaMarketSource

        with self.assertRaises(NotImplementedError):
            SinaMarketSource().fetch_severe_anomaly()


if __name__ == "__main__":
    unittest.main()
