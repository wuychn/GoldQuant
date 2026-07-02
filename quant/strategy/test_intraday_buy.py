"""盘中买入分时过滤测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from quant.constants import BUY_KIND_ASCENT
from quant.strategy.intraday import intraday_allows_buy, resolve_max_drop_from_high_pct


def _bars(closes: list[float], *, base_vol: float = 1000.0) -> list[dict]:
    out: list[dict] = []
    for i, close in enumerate(closes):
        opn = close - 0.03 if i > 0 else close - 0.01
        out.append(
            {
                "时间": f"2026-06-16 09:{30 + i}:00",
                "开盘": opn,
                "收盘": close,
                "成交量": base_vol + i * 50,
            }
        )
    return out


def _intra_cfg(**overrides: object) -> dict:
    base = {
        "intraday": {
            "skip_strength_min_day_chg": 5.0,
            "momentum_groups": {"enabled": True},
            "drawdown_from_high": {
                "default_pct": 3.0,
                "by_buy_kind": {"上升途中": 4.0},
                "strong_day_pct": 5.0,
                "strong_day_chg_pct": 5.0,
                "require_below_avg": True,
            },
        }
    }
    base["intraday"].update(overrides)
    return base


class IntradayStrengthTests(unittest.TestCase):
    def test_strong_day_passes(self) -> None:
        stock = {
            "股票代码": "000636",
            "盘口": {"最新": 71.0, "均价": 68.0, "最高": 71.5, "涨幅": 7.5},
            "分钟行情": _bars([68.0, 68.2, 68.5, 69.0, 69.5, 70.0, 70.5, 70.8, 71.0, 71.1, 71.2, 71.4]),
            "个股资金流": {"大单流入": "200 万元", "大单流出": "100 万元"},
        }
        ok, note = intraday_allows_buy(stock, _intra_cfg(), buy_kind=BUY_KIND_ASCENT)
        self.assertTrue(ok, note)

    @patch("quant.strategy.intraday.net_flow_improving", return_value=True)
    def test_net_flow_improving_passes(self, _mock: object) -> None:
        closes = [10.0 + i * 0.05 for i in range(12)]
        stock = {
            "股票代码": "000001",
            "盘口": {"最新": closes[-1], "均价": 10.2, "最高": closes[-1] + 0.1, "涨幅": 2.0},
            "分钟行情": _bars(closes),
            # 大单净 = 100 − 600 = -500 万（流出），但 net_flow_improving 放行
            "个股资金流": {"大单流入": "100 万元", "大单流出": "600 万元"},
        }
        ok, note = intraday_allows_buy(
            stock,
            _intra_cfg(skip_strength_min_day_chg=99.0),
        )
        self.assertTrue(ok, note)

    def test_below_avg_fails(self) -> None:
        stock = {
            "股票代码": "000001",
            "盘口": {"最新": 9.8, "均价": 10.0, "最高": 10.6, "涨幅": 1.0},
            "分钟行情": _bars([10.0, 10.05, 10.1, 10.0, 9.95, 9.9, 9.85, 9.82]),
            "个股资金流": {"大单流入": "200 万元", "大单流出": "100 万元"},
        }
        ok, note = intraday_allows_buy(stock, _intra_cfg())
        self.assertFalse(ok)
        self.assertIn("分时均价", note)

    def test_downward_slope_fails(self) -> None:
        closes = [10.0, 10.05, 10.1, 10.08, 10.05, 9.95, 9.9, 9.85, 9.82, 9.8, 9.78, 9.75]
        stock = {
            "股票代码": "000001",
            "盘口": {"最新": 9.75, "均价": 9.7, "最高": 10.2, "涨幅": 1.0},
            "分钟行情": _bars(closes),
            "个股资金流": {"大单流入": "300 万元", "大单流出": "100 万元"},
        }
        ok, note = intraday_allows_buy(
            stock,
            _intra_cfg(skip_strength_min_day_chg=99.0),
        )
        self.assertFalse(ok)
        self.assertIn("动量", note)

    def test_oscillating_upward_slope_passes(self) -> None:
        closes = [10.0, 10.08, 10.02, 10.06, 10.04, 10.07, 10.05, 10.12, 10.10, 10.14, 10.11, 10.16]
        stock = {
            "股票代码": "000001",
            "盘口": {"最新": 10.16, "均价": 10.0, "最高": 10.18, "涨幅": 2.0},
            "分钟行情": _bars(closes),
            "个股资金流": {"大单流入": "400 万元", "大单流出": "100 万元"},
        }
        ok, note = intraday_allows_buy(
            stock,
            _intra_cfg(skip_strength_min_day_chg=99.0),
        )
        self.assertTrue(ok, note)

    def test_drawdown_tier_by_buy_kind(self) -> None:
        intra = _intra_cfg()["intraday"]
        self.assertEqual(
            resolve_max_drop_from_high_pct(intra, buy_kind="上升途中", day_chg=3.0),
            4.0,
        )
        self.assertEqual(
            resolve_max_drop_from_high_pct(intra, buy_kind="上升途中", day_chg=7.0),
            5.0,
        )

    def test_deep_drawdown_ok_if_still_above_avg(self) -> None:
        """回撤超限但仍在均价上 → 不拒（require_below_avg）。"""
        stock = {
            "股票代码": "300285",
            "盘口": {"最新": 89.0, "均价": 85.0, "最高": 91.0, "涨幅": 13.0},
            "分钟行情": _bars([84.0, 85.0, 86.0, 87.0, 88.0, 88.5, 89.0, 89.2, 89.0, 89.1]),
            "个股资金流": {"大单流入": "600 万元", "大单流出": "100 万元"},
        }
        ok, note = intraday_allows_buy(stock, _intra_cfg(), buy_kind=BUY_KIND_ASCENT)
        self.assertTrue(ok, note)


if __name__ == "__main__":
    unittest.main()
