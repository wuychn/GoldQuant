"""build_daily 智能断点：incomplete_codes 完整性检查 / market_missing_dates 离线单测。"""

from __future__ import annotations

import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from scripts.data.build_daily import (
    add_no_bar_dates,
    incomplete_codes,
    last_cal_day_on_or_before,
    load_no_bar_map,
    market_missing_dates,
)


CAL = [
    "2026-07-20",
    "2026-07-21",
    "2026-07-22",
    "2026-07-23",
    "2026-07-24",
]


def _daily(rows: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["code", "date"])


class IncompleteCodesTests(unittest.TestCase):
    def test_last_cal_day_on_or_before(self):
        self.assertEqual(last_cal_day_on_or_before(CAL, "2026-07-23"), "2026-07-23")
        self.assertEqual(last_cal_day_on_or_before(CAL, "2026-07-25"), "2026-07-24")
        self.assertIsNone(last_cal_day_on_or_before([], "2026-07-24"))

    def test_empty_store_returns_all(self):
        codes = ["000001", "000002"]
        got = incomplete_codes(
            codes,
            start="2026-07-20",
            end="2026-07-24",
            daily=pd.DataFrame(),
            calendar=CAL,
        )
        self.assertEqual(got, codes)

    def test_only_missing_code(self):
        rows = [("000001", d) for d in CAL]
        got = incomplete_codes(
            ["000001", "000002"],
            start="2026-07-20",
            end="2026-07-24",
            daily=_daily(rows),
            calendar=CAL,
        )
        self.assertEqual(got, ["000002"])

    def test_missing_single_middle_day(self):
        """中间缺一天 → 待拉。"""
        rows = [("000001", d) for d in CAL if d != "2026-07-22"]
        got = incomplete_codes(
            ["000001"],
            start="2026-07-20",
            end="2026-07-24",
            daily=_daily(rows),
            calendar=CAL,
        )
        self.assertEqual(got, ["000001"])

    def test_missing_tail_days(self):
        rows = [("000001", d) for d in CAL[:-2]]
        got = incomplete_codes(
            ["000001"],
            start="2026-07-20",
            end="2026-07-24",
            daily=_daily(rows),
            calendar=CAL,
        )
        self.assertEqual(got, ["000001"])

    def test_missing_head_days(self):
        rows = [("000001", d) for d in CAL[2:]]
        got = incomplete_codes(
            ["000001"],
            start="2026-07-20",
            end="2026-07-24",
            daily=_daily(rows),
            calendar=CAL,
        )
        self.assertEqual(got, ["000001"])

    def test_listing_map_skips_pre_ipo_days(self):
        """有上市日时，上市前交易日不计入缺数。"""
        rows = [("000001", d) for d in CAL if d >= "2026-07-22"]
        got = incomplete_codes(
            ["000001"],
            start="2026-07-20",
            end="2026-07-24",
            daily=_daily(rows),
            calendar=CAL,
            listing_map={"000001": "2026-07-22"},
        )
        self.assertEqual(got, [])

    def test_complete_code_skipped(self):
        rows = [("000001", d) for d in CAL]
        got = incomplete_codes(
            ["000001"],
            start="2026-07-20",
            end="2026-07-24",
            daily=_daily(rows),
            calendar=CAL,
        )
        self.assertEqual(got, [])

    def test_preserves_order(self):
        rows = [("a", d) for d in CAL]
        got = incomplete_codes(
            ["b", "a", "c"],
            start="2026-07-20",
            end="2026-07-24",
            daily=_daily(rows),
            calendar=CAL,
        )
        self.assertEqual(got, ["b", "c"])

    def test_fetch_plan_uses_full_check_range(self):
        """缺尾部两天 → 补拉仍用完整检查区间 start~end。"""
        from scripts.data.build_daily import incomplete_fetch_plans

        rows = [("000001", d) for d in CAL[:-2]]
        plans = incomplete_fetch_plans(
            ["000001"],
            start="2026-07-20",
            end="2026-07-24",
            daily=_daily(rows),
            calendar=CAL,
        )
        self.assertEqual(plans, [("000001", "2026-07-20", "2026-07-24")])

    def test_fetch_plan_respects_no_bar(self):
        """无行情豁免日后，不再因该日进入待拉。"""
        from scripts.data.build_daily import incomplete_fetch_plans

        rows = [("000001", d) for d in CAL if d != "2026-07-22"]
        plans = incomplete_fetch_plans(
            ["000001"],
            start="2026-07-20",
            end="2026-07-24",
            daily=_daily(rows),
            calendar=CAL,
            no_bar_map={"000001": {"2026-07-22"}},
        )
        self.assertEqual(plans, [])

    def test_fetch_plan_single_middle_day_still_full_range(self):
        from scripts.data.build_daily import incomplete_fetch_plans

        rows = [("000001", d) for d in CAL if d != "2026-07-22"]
        plans = incomplete_fetch_plans(
            ["000001"],
            start="2026-07-20",
            end="2026-07-24",
            daily=_daily(rows),
            calendar=CAL,
        )
        self.assertEqual(plans, [("000001", "2026-07-20", "2026-07-24")])

    def test_fetch_plan_empty_code_full_window(self):
        from scripts.data.build_daily import incomplete_fetch_plans

        plans = incomplete_fetch_plans(
            ["000002"],
            start="2026-07-20",
            end="2026-07-24",
            daily=_daily([("000001", d) for d in CAL]),
            calendar=CAL,
        )
        self.assertEqual(plans, [("000002", "2026-07-20", "2026-07-24")])


class MarketMissingDatesTests(unittest.TestCase):
    @patch("scripts.data.maintain.scan_missing_dates")
    def test_filters_start(self, mock_scan):
        mock_scan.return_value = ["2026-07-20", "2026-07-22", "2026-07-24"]
        miss = market_missing_dates(start="2026-07-22", end="2026-07-24")
        self.assertEqual(miss, ["2026-07-22", "2026-07-24"])
        mock_scan.assert_called_once_with("2026-07-24")

    @patch("scripts.data.maintain.scan_missing_dates")
    def test_gap_fill_needed_when_missing(self, mock_scan):
        mock_scan.return_value = ["2026-07-22"]
        self.assertEqual(
            market_missing_dates(start="2026-07-20", end="2026-07-24"),
            ["2026-07-22"],
        )

    @patch("scripts.data.maintain.scan_missing_dates")
    def test_gap_fill_skip_when_complete(self, mock_scan):
        mock_scan.return_value = []
        self.assertEqual(market_missing_dates(start="2026-07-20", end="2026-07-24"), [])


class UpdateFailedTests(unittest.TestCase):
    """``_update_failed``：已解决（成功/确认无行情）清出；网络失败 retries+1。"""

    def _prep(self, td: str, entries: list[dict]) -> Path:
        p = Path(td) / "build_failed.jsonl"
        p.write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in entries)
            + ("\n" if entries else ""),
            encoding="utf-8",
        )
        return p

    @patch("scripts.data.build_daily._failed_file")
    def test_confirmed_no_bar_clears_failed(self, mock_ff):
        """确认无行情 (0, None) 应清出 failed（修前永留 → --retry-failed 反复重拉）。"""
        with TemporaryDirectory() as td:
            p = self._prep(td, [{"code": "000999", "reason": "net", "retries": 2, "dead": False, "ts": "x"}])
            mock_ff.return_value = p
            from scripts.data.build_daily import _update_failed

            failed = _update_failed({"000999": (0, None)}, dead_threshold=5)
        self.assertNotIn("000999", failed)

    @patch("scripts.data.build_daily._failed_file")
    def test_network_failure_increments(self, mock_ff):
        with TemporaryDirectory() as td:
            p = self._prep(td, [{"code": "000998", "reason": "net", "retries": 2, "dead": False, "ts": "x"}])
            mock_ff.return_value = p
            from scripts.data.build_daily import _update_failed

            failed = _update_failed({"000998": (0, "RemoteDisconnected")}, dead_threshold=5)
        self.assertEqual(failed["000998"]["retries"], 3)
        self.assertFalse(failed["000998"]["dead"])

    @patch("scripts.data.build_daily._failed_file")
    def test_network_failure_hits_dead_threshold(self, mock_ff):
        with TemporaryDirectory() as td:
            p = self._prep(td, [{"code": "000997", "reason": "net", "retries": 4, "dead": False, "ts": "x"}])
            mock_ff.return_value = p
            from scripts.data.build_daily import _update_failed

            failed = _update_failed({"000997": (0, "err")}, dead_threshold=5)
        self.assertTrue(failed["000997"]["dead"])

    @patch("scripts.data.build_daily._failed_file")
    def test_success_clears(self, mock_ff):
        with TemporaryDirectory() as td:
            p = self._prep(td, [{"code": "000996", "reason": "net", "retries": 1, "dead": False, "ts": "x"}])
            mock_ff.return_value = p
            from scripts.data.build_daily import _update_failed

            failed = _update_failed({"000996": (100, None)}, dead_threshold=5)
        self.assertNotIn("000996", failed)


class NoBarLockTests(unittest.TestCase):
    """``add_no_bar_dates`` 并发 RMW 不丢更新（``_no_bar_lock`` 验证）。"""

    @patch("scripts.data.build_daily._no_bar_path")
    def test_concurrent_add_no_lost_updates(self, mock_path):
        with TemporaryDirectory() as td:
            p = Path(td) / "no_bar.json"
            mock_path.return_value = p
            code = "000001"

            def add(i: int) -> int:
                return add_no_bar_dates(code, {f"D{i * 5 + j:03d}" for j in range(5)})

            with ThreadPoolExecutor(max_workers=8) as ex:
                list(ex.map(add, range(20)))
            mp = load_no_bar_map()
        self.assertEqual(len(mp.get(code, set())), 100)  # 20×5 全保留，无丢失


if __name__ == "__main__":
    unittest.main()