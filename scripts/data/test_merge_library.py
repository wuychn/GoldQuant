"""merge_library 合并逻辑单测：schema 归一、重叠去重(update 优先)、adj/calendar 并集。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from quant.data.schema import DAILY_RAW_COLUMNS
from quant.data.store import read_adj_factor, read_calendar, read_daily_raw, write_adj_factor, write_calendar, write_daily_raw
from quant.store.paths import override_quant_home


class _Home:
    def __init__(self, root: Path):
        (root / "store").mkdir(parents=True, exist_ok=True)
        self.root = root

    def write_raw(self, df: pd.DataFrame) -> None:
        with override_quant_home(self.root):
            write_daily_raw(df)

    def read_raw(self) -> pd.DataFrame:
        with override_quant_home(self.root):
            return read_daily_raw()

    def write_adj(self, df: pd.DataFrame) -> None:
        with override_quant_home(self.root):
            write_adj_factor(df)

    def read_adj(self) -> pd.DataFrame:
        with override_quant_home(self.root):
            return read_adj_factor()

    def write_cal(self, days: list[str]) -> None:
        with override_quant_home(self.root):
            write_calendar(days)

    def read_cal(self) -> list[str]:
        with override_quant_home(self.root):
            return read_calendar()


def _offline_raw() -> pd.DataFrame:
    """build_daily 风格：stock_zh_a_hist 行，无 name/pre_close/float_mv/total_mv。"""
    return pd.DataFrame(
        {
            "code": ["000001", "000001"],
            "date": ["2026-07-01", "2026-07-02"],
            "open": [10.0, 10.1],
            "high": [10.5, 10.6],
            "low": [9.9, 10.0],
            "close": [10.2, 10.3],
            "volume": [1e6, 1.1e6],
            "amount": [1e8, 1.1e8],
            "turnover_rate": [1.0, 1.1],
        }
    )


def _daily_raw() -> pd.DataFrame:
    """update_daily 风格：spot 行，13 列齐全；覆盖 07-02、新增 07-03。"""
    return pd.DataFrame(
        {
            "code": ["000001", "000001"],
            "date": ["2026-07-02", "2026-07-03"],
            "name": ["A", "A"],
            "open": [10.0, 10.4],
            "high": [10.7, 10.8],
            "low": [9.9, 10.2],
            "close": [10.4, 10.5],
            "pre_close": [10.3, 10.4],
            "volume": [1.2e6, 1.3e6],
            "amount": [1.2e8, 1.3e8],
            "turnover_rate": [1.2, 1.3],
            "float_mv": [1e10, 1e10],
            "total_mv": [2e10, 2e10],
        }
    )


class MergeDailyRawTests(unittest.TestCase):
    def test_schema_unify_overlap_daily_wins(self):
        from scripts.data.merge_library import _merge_daily_raw

        with tempfile.TemporaryDirectory() as td:
            offline = _Home(Path(td) / "offline")
            daily = _Home(Path(td) / "daily")
            out = _Home(Path(td) / "out")
            offline.write_raw(_offline_raw())
            daily.write_raw(_daily_raw())

            n = _merge_daily_raw(offline.root, daily.root, out.root)

            m = out.read_raw()
            # 1) 归一 13 列
            self.assertEqual(list(m.columns), list(DAILY_RAW_COLUMNS))
            # 2) 并集去重：07-01(offline) / 07-02(daily 覆盖) / 07-03(daily)
            keys = {k for k in zip(m["code"], m["date"])}
            self.assertEqual(
                keys,
                {("000001", "2026-07-01"), ("000001", "2026-07-02"), ("000001", "2026-07-03")},
            )
            self.assertEqual(n, 3)
            # 3) 重叠日 07-02 由 daily 覆盖（close=10.4、name=A，非 offline 的 10.3/NaN）
            r2 = m[m["date"] == "2026-07-02"].iloc[0]
            self.assertEqual(float(r2["close"]), 10.4)
            self.assertEqual(str(r2["name"]).strip(), "A")
            # 4) offline 行 07-01 无 pre_close/float_mv → NaN
            r1 = m[m["date"] == "2026-07-01"].iloc[0]
            self.assertTrue(pd.isna(r1["pre_close"]))
            self.assertTrue(pd.isna(r1["float_mv"]))

    def test_merge_adj_daily_wins(self):
        from scripts.data.merge_library import _merge_adj

        with tempfile.TemporaryDirectory() as td:
            offline = _Home(Path(td) / "offline")
            daily = _Home(Path(td) / "daily")
            out = _Home(Path(td) / "out")
            offline.write_adj(
                pd.DataFrame(
                    {"code": ["000001", "000001"], "date": ["2026-07-01", "2026-07-02"], "hfq_factor": [1.0, 1.1]}
                )
            )
            daily.write_adj(
                pd.DataFrame({"code": ["000001"], "date": ["2026-07-02"], "hfq_factor": [1.2]})
            )

            _merge_adj(offline.root, daily.root, out.root)

            a = out.read_adj()
            a = a.sort_values("date")
            self.assertEqual(list(a["date"]), ["2026-07-01", "2026-07-02"])
            self.assertEqual(list(a["hfq_factor"]), [1.0, 1.2])  # 07-02 daily 覆盖

    def test_merge_calendar_union(self):
        from scripts.data.merge_library import _merge_calendar

        with tempfile.TemporaryDirectory() as td:
            offline = _Home(Path(td) / "offline")
            daily = _Home(Path(td) / "daily")
            out = _Home(Path(td) / "out")
            offline.write_cal(["2026-07-01", "2026-07-02"])
            daily.write_cal(["2026-07-02", "2026-07-03"])

            _merge_calendar(offline.root, daily.root, out.root)

            self.assertEqual(out.read_cal(), ["2026-07-01", "2026-07-02", "2026-07-03"])


if __name__ == "__main__":
    unittest.main()
