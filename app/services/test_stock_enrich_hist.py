"""enrich 历史行情截断条数。"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.services.stock_enrich import _hist_max_bars, _rows_last_n_trade_days


class HistMaxBarsTests(unittest.TestCase):
    def test_default_scoring_bars_from_settings(self) -> None:
        settings = MagicMock()
        settings.QUANT_HIST_SCORING_MAX_BARS = 90
        self.assertEqual(_hist_max_bars(settings, None), 90)

    def test_explicit_override(self) -> None:
        settings = MagicMock()
        settings.QUANT_HIST_SCORING_MAX_BARS = 90
        self.assertEqual(_hist_max_bars(settings, 48), 48)

    def test_rows_last_n(self) -> None:
        rows = [{"日期": f"2026-05-{i:02d}", "收盘": float(i)} for i in range(1, 31)]
        out = _rows_last_n_trade_days(rows, n=90)
        self.assertEqual(len(out), 30)


if __name__ == "__main__":
    unittest.main()
