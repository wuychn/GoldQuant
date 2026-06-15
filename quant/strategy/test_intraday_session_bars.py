"""连续竞价分钟 K 解析测试（基于真实字段形态）。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from quant.strategy.intraday import is_continuous_auction_minute, session_minute_bars


class IntradaySessionBarsTests(unittest.TestCase):
    def test_time_filter_excludes_auction_and_includes_session(self) -> None:
        self.assertFalse(is_continuous_auction_minute("2026-06-12 09:15:00"))
        self.assertFalse(is_continuous_auction_minute("2026-06-12 09:29:00"))
        self.assertTrue(is_continuous_auction_minute("2026-06-12 09:30:00"))
        self.assertTrue(is_continuous_auction_minute("2026-06-12 09:31:00"))
        self.assertTrue(is_continuous_auction_minute("2026-06-12 10:15:00"))
        self.assertTrue(is_continuous_auction_minute("2026-06-12 11:30:00"))
        self.assertFalse(is_continuous_auction_minute("2026-06-12 12:00:00"))
        self.assertTrue(is_continuous_auction_minute("2026-06-12 13:00:00"))
        self.assertTrue(is_continuous_auction_minute("2026-06-12 15:00:00"))

    def test_session_bars_skip_0915_on_synthetic(self) -> None:
        stock = {
            "分钟行情": [
                {"时间": "2026-06-12 09:15:00", "开盘": 7.5, "收盘": 7.5, "成交量": 0},
                {"时间": "2026-06-12 09:31:00", "开盘": 7.37, "收盘": 7.14, "成交量": 198770},
            ]
        }
        bars = session_minute_bars(stock)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["time"], "2026-06-12 09:31:00")
        self.assertEqual(bars[0]["vol"], 198770.0)

    def test_real_snapshot_1506(self) -> None:
        p = Path.home() / ".quant/daily/2026-06-12/raw/during_1506.json"
        if not p.is_file():
            self.skipTest("no local during snapshot")
        raw = json.loads(p.read_text(encoding="utf-8"))
        data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        stock = (data.get("自选股") or [None])[0]
        self.assertIsInstance(stock, dict)
        bars = session_minute_bars(stock)
        self.assertGreaterEqual(len(bars), 200)
        self.assertTrue(bars[0]["time"].endswith("09:30:00"))
        self.assertFalse(any(b["time"].endswith("09:15:00") for b in bars))


if __name__ == "__main__":
    unittest.main()
