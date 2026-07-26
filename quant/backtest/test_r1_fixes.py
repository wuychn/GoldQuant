"""r1 修补回归测试（unittest，可被 ``unittest discover`` 收集）。

锁定：_inject_state 字段合并、停牌启发式、日历、benchmark、复权口径回滚。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from quant.backtest.engine import enrich_holdings_with_payload, index_payload_stocks
from quant.backtest.simulator import _inject_state
from quant.data.suspension import is_suspended_from_row
from quant.research.state import MemoryPortfolioState


def _rich_row(code: str, name: str = "甲股") -> dict:
    return {
        "股票代码": code,
        "股票名称": name,
        "盘口": {"最新": 10.5, "今开": 10.3, "最高": 10.8, "最低": 10.1},
        "历史行情": [{"日期": "2024-01-01", "收盘": 10.0}, {"日期": "2024-01-02", "收盘": 10.5}],
        "技术指标": {"last_close": 10.5},
        "上市时间": "2010-01-01",
        "所属概念": ["概念A"],
    }


def _thin_watchlist(code: str, score: float = 80.0) -> dict:
    return {
        "股票代码": code,
        "股票名称": "甲股",
        "评分": score,
        "加入自选原因": "动量加速",
        "战法": "主升",
    }


def _curve(equities: list[float], dates: list[str]):
    class _B:
        pass

    b = _B()
    b.equity_curve = [{"date": d, "equity": e} for d, e in zip(dates, equities)]
    return b


def _reset_calendar_cache():
    from quant.data import calendar

    calendar._cached_days = None
    calendar._cached_mtime = None
    calendar._last_check_mono = 0.0
    calendar._sorted_days = None


class InjectStateTests(unittest.TestCase):
    def test_index_payload_stocks_prefers_rich(self):
        payload = {"自选股": [_rich_row("000001")], "_observe_enriched": [_rich_row("000002")]}
        idx = index_payload_stocks(payload)
        self.assertIn("000001", idx)
        self.assertIn("000002", idx)
        self.assertEqual(idx["000001"]["盘口"]["最新"], 10.5)

    def test_inject_state_preserves_market_data(self):
        payload = {"自选股": [_rich_row("000001")]}
        mem = MemoryPortfolioState(cash=1_000_000)
        mem.watchlist = [_thin_watchlist("000001", score=85.0)]
        out = _inject_state(payload, mem)
        row = out["自选股"][0]
        self.assertEqual(row["盘口"]["最新"], 10.5)
        self.assertEqual(len(row["历史行情"]), 2)
        self.assertEqual(row["评分"], 85.0)
        self.assertEqual(row["加入自选原因"], "动量加速")
        self.assertEqual(row["股票代码"], "000001")

    def test_inject_state_thin_only_falls_back(self):
        payload = {"自选股": []}
        mem = MemoryPortfolioState(cash=1_000_000)
        mem.watchlist = [_thin_watchlist("000999")]
        out = _inject_state(payload, mem)
        self.assertEqual(out["自选股"][0]["股票代码"], "000999")

    def test_enrich_holdings_with_payload(self):
        payload = {"自选股": [_rich_row("000001")]}
        holdings = [{"股票代码": "000001", "买入价": 10.0, "持仓股数": 100, "买入日期": "2024-01-01"}]
        h = enrich_holdings_with_payload(holdings, payload)[0]
        self.assertEqual(h["买入价"], 10.0)
        self.assertEqual(h["持仓股数"], 100)
        self.assertEqual(h["盘口"]["最新"], 10.5)
        self.assertEqual(len(h["历史行情"]), 2)


class SuspensionTests(unittest.TestCase):
    def test_not_bypassed_by_technical_indicators(self):
        suspended = {"股票代码": "000001", "盘口": {}, "技术指标": {"last_close": 10.0}}
        self.assertTrue(is_suspended_from_row(suspended))
        self.assertTrue(is_suspended_from_row({"股票代码": "000002"}))
        active = {"股票代码": "000003", "盘口": {"最新": 10.0, "今开": 9.9}, "技术指标": {}}
        self.assertFalse(is_suspended_from_row(active))

    def test_handles_non_float_quote_types(self):
        self.assertFalse(is_suspended_from_row({"盘口": {"最新": "10.5"}}))
        self.assertFalse(is_suspended_from_row({"盘口": {"最新": "1,234.56"}}))
        self.assertFalse(is_suspended_from_row({"盘口": {"今开": "9.9"}}))
        try:
            import numpy as np

            self.assertFalse(is_suspended_from_row({"盘口": {"最新": np.float64(10.5)}}))
        except ImportError:
            pass
        self.assertTrue(is_suspended_from_row({"盘口": {"最新": "-", "今开": ""}}))
        self.assertTrue(is_suspended_from_row({"盘口": {"最新": 0, "今开": 0.0}}))


class BenchmarkTests(unittest.TestCase):
    def test_excess_is_total_return_difference(self):
        import quant.research.metrics.benchmark as bm

        dates = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]
        broker = _curve([100.0, 105.0, 110.0, 120.0], dates)
        closes = {"2024-01-01": 10.0, "2024-01-02": 10.2, "2024-01-03": 10.5, "2024-01-04": 11.0}
        with patch.object(bm, "_fetch_index_closes", return_value=closes):
            res = bm.compute_benchmark_metrics(broker, benchmark_symbol="000300", trading_days=4)
        self.assertAlmostEqual(res["benchmark_total_return"], 0.10, places=6)
        self.assertAlmostEqual(res["excess_return_vs_benchmark"], 0.10, places=6)

    def test_series_stay_aligned_when_index_starts_late(self):
        import quant.research.metrics.benchmark as bm

        dates = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]
        broker = _curve([100.0, 100.0, 110.0, 121.0], dates)
        closes = {"2024-01-03": 10.0, "2024-01-04": 11.0}
        with patch.object(bm, "_fetch_index_closes", return_value=closes):
            res = bm.compute_benchmark_metrics(broker, benchmark_symbol="000300", trading_days=2)
        self.assertAlmostEqual(res["benchmark_total_return"], 0.10, places=6)
        self.assertAlmostEqual(res["excess_return_vs_benchmark"], 0.0, places=6)

    def test_tracking_error_non_negative(self):
        import quant.research.metrics.benchmark as bm

        dates = [f"2024-01-{i:02d}" for i in range(1, 11)]
        eq = [100.0 * (1.01 ** i) for i in range(10)]
        broker = _curve(eq, dates)
        closes = {d: 10.0 * (1.005 ** i) for i, d in enumerate(dates)}
        with patch.object(bm, "_fetch_index_closes", return_value=closes):
            res = bm.compute_benchmark_metrics(broker, benchmark_symbol="000300", trading_days=10)
        self.assertIsNotNone(res["tracking_error"])
        self.assertGreaterEqual(res["tracking_error"], 0.0)
        self.assertLess(res["tracking_error"], 0.05)


class CalendarTests(unittest.TestCase):
    def tearDown(self):
        _reset_calendar_cache()

    def test_fetch_failure_does_not_write_empty_cache(self):
        from quant.data import calendar

        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "cache" / "trade_calendar.json"
            with patch.object(calendar, "_calendar_path", return_value=cache), \
                 patch.dict("sys.modules", {"akshare": None}):
                days = calendar._fetch_and_cache()
            self.assertEqual(days, set())
            self.assertFalse(cache.exists(), "抓取失败仍写了缓存文件")

    def test_reloads_after_cache_file_changes(self):
        from quant.data import calendar

        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "trade_calendar.json"
            cache.write_text(json.dumps(["2024-01-02"]), encoding="utf-8")
            with patch.object(calendar, "_calendar_path", return_value=cache):
                _reset_calendar_cache()
                first = calendar._load_calendar_fresh()
                self.assertIn(date(2024, 1, 2), first)
                self.assertNotIn(date(2024, 1, 3), first)

                cache.write_text(json.dumps(["2024-01-02", "2024-01-03"]), encoding="utf-8")
                st = cache.stat()
                os.utime(cache, (st.st_atime + 10, st.st_mtime + 10))
                # 绕过节流
                calendar._last_check_mono = 0.0
                second = calendar._load_calendar_fresh()
                self.assertIn(date(2024, 1, 3), second)

    def test_trading_days_between_uses_set_not_per_day_stat(self):
        """区间计数应一次取集合；合成日历上结果可手算。"""
        from quant.data import calendar

        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "cal.json"
            days = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
            cache.write_text(json.dumps(days), encoding="utf-8")
            with patch.object(calendar, "_calendar_path", return_value=cache):
                _reset_calendar_cache()
                n = calendar.trading_days_between(date(2024, 1, 2), date(2024, 1, 5))
                # (2, 5] → 3,4,5
                self.assertEqual(n, 3)
                lst = calendar.trading_day_list(date(2024, 1, 2), date(2024, 1, 4))
                self.assertEqual(lst, [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)])


class AdjustTests(unittest.TestCase):
    def test_close_provider_uses_unadjusted(self):
        """买入价=盘口原始价；估值必须 adjust=\"\"，不能用 qfq。"""
        import inspect

        from quant.backtest import engine as eng

        src = inspect.getsource(eng._make_close_provider)
        self.assertIn('adjust=""', src)
        self.assertNotIn('adjust="qfq"', src)


if __name__ == "__main__":
    unittest.main()
