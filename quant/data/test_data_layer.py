"""P0 数据地基单元测试（离线，合成数据，不依赖网络/pyarrow 落盘）。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

# 用临时 QUANT_HOME 隔离，避免污染真实数据
_TMP = Path(tempfile.mkdtemp(prefix="gq_test_"))


class _QuantHomeStub:
    @staticmethod
    def quant_home() -> Path:
        return _TMP


def _install_stub():
    import quant.store.paths as p

    p.QUANT_HOME = _TMP
    p.quant_home = _QuantHomeStub.quant_home  # type: ignore[assignment]


_install_stub()


class UniverseTests(unittest.TestCase):
    def setUp(self):
        # 构造合成 daily_raw（不复权）
        import pandas as pd

        self._pd = pd
        # 200 个连续工作日作为 000001 的历史
        dates_001 = pd.bdate_range("2025-04-01", periods=200).strftime("%Y-%m-%d").tolist()
        rows = []
        # 000001：上市 200 日，日均成交额 2 亿，非 ST，当日成交 -> 通过
        for d in dates_001:
            rows.append({
                "code": "000001", "date": d, "name": "平安银行",
                "open": 12.0, "high": 12.5, "low": 11.8, "close": 12.3,
                "pre_close": 12.1, "volume": 1000000, "amount": 2e8,
                "turnover_rate": 1.0, "float_mv": 1e10, "total_mv": 2e10,
            })
        as_of = dates_001[-1]  # 000001 最后一个日期作为评估日
        self.as_of = as_of
        # 000002：ST，应剔除
        rows.append({
            "code":"000002", "date": as_of, "name":"*ST测试", "open":1,"high":1,
            "low":1,"close":1,"pre_close":1,"volume":1000,"amount":1e7,
            "turnover_rate":1,"float_mv":1e9,"total_mv":1e9,
        })
        # 000003：上市不足 120 日（只在最后 50 个交易日有数据），应剔除
        for d in dates_001[-50:]:
            rows.append({
                "code":"000003","date":d,"name":"新股",
                "open":5,"high":5,"low":5,"close":5,"pre_close":5,
                "volume":1000,"amount":2e8,"turnover_rate":1,"float_mv":1e9,"total_mv":1e9,
            })
        # 000004：当日停牌（volume=0），应剔除
        rows.append({
            "code":"000004","date": as_of,"name":"停牌股","open":0,"high":0,
            "low":0,"close":3,"pre_close":3,"volume":0,"amount":0,
            "turnover_rate":0,"float_mv":1e9,"total_mv":1e9,
        })
        # 000005：日均成交额 < 5000 万，应剔除
        for d in dates_001:
            rows.append({
                "code":"000005","date":d,"name":"低流动性",
                "open":2,"high":2,"low":2,"close":2,"pre_close":2,
                "volume":100,"amount":3e6,"turnover_rate":0.1,"float_mv":1e8,"total_mv":1e8,
            })
        self.daily = self._pd.DataFrame(rows)

    def test_st_excluded(self):
        from quant.data.universe import build_universe_snapshot

        snap = build_universe_snapshot(self.as_of, daily=self.daily)
        st = snap[snap["code"] == "000002"].iloc[0]
        self.assertFalse(st["included"], "ST 应被剔除")

    def test_new_listing_excluded(self):
        from quant.data.universe import build_universe_snapshot

        snap = build_universe_snapshot(self.as_of, daily=self.daily)
        new = snap[snap["code"] == "000003"].iloc[0]
        self.assertFalse(new["included"], "上市不足 120 日应剔除")

    def test_suspended_excluded(self):
        from quant.data.universe import build_universe_snapshot

        snap = build_universe_snapshot(self.as_of, daily=self.daily)
        susp = snap[snap["code"] == "000004"].iloc[0]
        self.assertFalse(susp["included"], "当日停牌应剔除")

    def test_low_adv_excluded(self):
        from quant.data.universe import build_universe_snapshot

        snap = build_universe_snapshot(self.as_of, daily=self.daily)
        low = snap[snap["code"] == "000005"].iloc[0]
        self.assertFalse(low["included"], "低流动性应剔除")

    def test_normal_included(self):
        from quant.data.universe import build_universe_snapshot

        snap = build_universe_snapshot(self.as_of, daily=self.daily)
        ok = snap[snap["code"] == "000001"].iloc[0]
        self.assertTrue(ok["included"], "正常票应通过")

    def test_pit_no_future_leak(self):
        """universe(T) 只用 <= T 的数据：取 000001 第 150 个日期为评估日，
        删掉之后所有数据，000001 仍应有 150 日数据 >= 120 通过过滤。"""
        from quant.data.universe import build_universe_snapshot

        as_of = sorted(self.daily["date"].unique())[149]
        cut = self.daily[self.daily["date"] <= as_of]
        snap = build_universe_snapshot(as_of, daily=cut)
        codes = snap.loc[snap["included"], "code"].tolist()
        self.assertIn("000001", codes)


class AdjustTests(unittest.TestCase):
    def test_detect_ex_dividend(self):
        import pandas as pd

        from quant.data.adjust import detect_ex_dividend_codes

        spot = pd.DataFrame({
            "code": ["000001", "000002"],
            "pre_close": [12.0, 5.0],  # 000001 昨收 12，库中前一日 close 10 → 除权
        })
        prev_map = {"000001": 10.0, "000002": 5.0}
        ex = detect_ex_dividend_codes(spot, prev_map)
        self.assertEqual(ex, ["000001"])

    def test_apply_hfq(self):
        import pandas as pd

        from quant.data.adjust import apply_hfq

        daily = pd.DataFrame({
            "code": ["000001", "000001"],
            "date": ["2026-02-14", "2026-02-15"],
            "open": [10.0, 7.0], "high": [10.5, 7.2], "low": [9.8, 6.8],
            "close": [10.0, 7.0], "pre_close": [9.9, 10.0],
        })
        adj = pd.DataFrame({
            "code": ["000001", "000001"],
            "date": ["2026-02-14", "2026-02-15"],
            "hfq_factor": [1.0, 1.5],  # 02-15 除权，因子 1.5
        })
        hfq = apply_hfq(daily, adj)
        # 02-15 close 7.0 × 1.5 = 10.5，与 02-14 close 10.0 连续（除权后复权）
        self.assertAlmostEqual(float(hfq.iloc[1]["close"]), 10.5)
        self.assertAlmostEqual(float(hfq.iloc[0]["close"]), 10.0)


class CalendarTests(unittest.TestCase):
    def test_weekend_not_trading(self):
        from quant.data.calendar import is_trading_day

        self.assertFalse(is_trading_day(date(2026, 7, 25)))  # 周六

    def test_trading_days_between(self):
        from quant.data.calendar import trading_days_between

        # 2026-07-20(周一) ~ 2026-07-24(周五) 区间 (start,end] = 4 个交易日
        n = trading_days_between(date(2026, 7, 20), date(2026, 7, 24))
        self.assertGreaterEqual(n, 3)


if __name__ == "__main__":
    unittest.main()
