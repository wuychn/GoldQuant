"""盘中买入分时过滤测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from quant.strategy.intraday import intraday_allows_buy


def _bars(closes: list[float], *, base_vol: float = 1000.0) -> list[dict]:
    out: list[dict] = []
    for i, close in enumerate(closes):
        opn = closes[i - 1] if i > 0 else close - 0.01
        out.append(
            {
                "时间": f"2026-06-16 09:{30 + i}:00",
                "开盘": opn,
                "收盘": close,
                "成交量": base_vol + i * 10,
            }
        )
    return out


class IntradayStrengthTests(unittest.TestCase):
    def test_strong_day_passes(self) -> None:
        stock = {
            "股票代码": "000636",
            "盘口": {"最新": 71.0, "均价": 68.0, "最高": 71.5, "涨幅": 7.5},
            "分钟行情": _bars([68.0, 68.2, 68.5, 69.0, 69.5, 70.0, 70.5, 71.0]),
        }
        cfg = {"intraday": {"skip_strength_min_day_chg": 5.0}}
        ok, note = intraday_allows_buy(stock, cfg)
        self.assertTrue(ok, note)

    @patch("quant.strategy.intraday.net_flow_improving", return_value=True)
    def test_net_flow_improving_passes(self, _mock: object) -> None:
        closes = [10.0 + i * 0.05 for i in range(12)]
        stock = {
            "股票代码": "000001",
            "盘口": {"最新": closes[-1], "均价": 10.2, "最高": closes[-1] + 0.1, "涨幅": 2.0},
            "分钟行情": _bars(closes),
            "个股资金流": {"净额": "-500 万元"},
        }
        cfg = {
            "intraday": {
                "skip_strength_min_day_chg": 99.0,
                "min_above_vwap_ratio": 0.5,
            }
        }
        ok, note = intraday_allows_buy(stock, cfg)
        self.assertTrue(ok, note)

    def test_low_above_vwap_ratio_fails(self) -> None:
        closes = [10.0, 9.9, 9.8, 9.7, 9.6, 10.5, 10.4, 10.3]
        stock = {
            "股票代码": "000001",
            "盘口": {"最新": 10.3, "均价": 10.0, "最高": 10.6, "涨幅": 1.0},
            "分钟行情": _bars(closes),
            "个股资金流": {"净额": "100 万元"},
        }
        cfg = {"intraday": {"min_above_vwap_ratio": 0.65, "skip_strength_min_day_chg": 99.0}}
        ok, note = intraday_allows_buy(stock, cfg)
        self.assertFalse(ok)
        self.assertIn("均价上方", note)


if __name__ == "__main__":
    unittest.main()
