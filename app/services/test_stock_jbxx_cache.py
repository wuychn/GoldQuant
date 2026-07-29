"""个股基本信息周缓存单元测试。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from quant.services.jbxx_cache import (
    StockJbxxCache,
    fetch_jbxx_cached,
    fetch_stock_industry,
    industry_from_jbxx,
)
from common.timeutil import cn_datetime_str, cn_now


class StockJbxxCacheTests(unittest.TestCase):
    def test_put_and_lookup_within_week(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stock_jbxx.json"
            cache = StockJbxxCache(path=path, ttl_days=7)
            data = {"总股本": "100", "上市时间": "19910403"}
            cache.put("000001", data)
            hit, loaded = cache.lookup("000001")
            self.assertTrue(hit)
            self.assertEqual(loaded, data)

            cache2 = StockJbxxCache(path=path, ttl_days=7)
            hit2, loaded2 = cache2.lookup("000001")
            self.assertTrue(hit2)
            self.assertEqual(loaded2, data)

    def test_expired_entry_misses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stock_jbxx.json"
            cache = StockJbxxCache(path=path, ttl_days=7)
            stale = cn_datetime_str(cn_now() - timedelta(days=8))
            cache._data = {
                "stocks": {
                    "600519": {
                        "data": {"总股本": "1"},
                        "fetched_at": stale,
                    }
                }
            }
            hit, _ = cache.lookup("600519")
            self.assertFalse(hit)

    def test_lookup_stale_returns_expired_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stock_jbxx.json"
            cache = StockJbxxCache(path=path, ttl_days=7)
            stale = cn_datetime_str(cn_now() - timedelta(days=8))
            cache._data = {
                "stocks": {
                    "000636": {
                        "data": {"行业": "元件", "总股本": "1"},
                        "fetched_at": stale,
                    }
                }
            }
            self.assertIsNone(cache.lookup_stale("000001"))
            self.assertEqual(cache.lookup_stale("000636"), {"行业": "元件", "总股本": "1"})

    def test_fetch_jbxx_cached_falls_back_to_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stock_jbxx.json"
            cache = StockJbxxCache(path=path, ttl_days=7)
            stale = cn_datetime_str(cn_now() - timedelta(days=8))
            cache._data = {
                "stocks": {
                    "000636": {
                        "data": {"行业": "元件"},
                        "fetched_at": stale,
                    }
                }
            }
            with patch("quant.services.jbxx_cache._fetch_jbxx_live", return_value=None):
                out = fetch_jbxx_cached("000636", file_cache=cache)
            self.assertEqual(out, {"行业": "元件"})

    def test_fetch_stock_industry_from_stale_when_live_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stock_jbxx.json"
            cache = StockJbxxCache(path=path, ttl_days=7)
            stale = cn_datetime_str(cn_now() - timedelta(days=8))
            cache._data = {
                "stocks": {
                    "603078": {
                        "data": {"行业": "电子化学品"},
                        "fetched_at": stale,
                    }
                }
            }
            with patch("quant.services.jbxx_cache._fetch_jbxx_live", return_value=None):
                ind = fetch_stock_industry("603078", file_cache=cache)
            self.assertEqual(ind, "电子化学品")

    def test_industry_from_jbxx(self) -> None:
        self.assertEqual(industry_from_jbxx({"行业": "元件"}), "元件")
        self.assertIsNone(industry_from_jbxx({}))
        self.assertIsNone(industry_from_jbxx(None))


if __name__ == "__main__":
    unittest.main()
