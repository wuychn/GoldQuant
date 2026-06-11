"""个股基本信息周缓存单元测试。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.services.stock_jbxx_cache import StockJbxxCache

_TZ = ZoneInfo("Asia/Shanghai")


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
            stale = (datetime.now(_TZ) - timedelta(days=8)).isoformat()
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


if __name__ == "__main__":
    unittest.main()
