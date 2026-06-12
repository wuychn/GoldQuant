"""个股概念日缓存单元测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.stock_concept_cache import DailyConceptCache


class DailyConceptCacheTests(unittest.TestCase):
    def test_put_and_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stock_concepts.json"
            cache = DailyConceptCache("2026-06-11", path=path)
            self.assertFalse(cache.lookup("000001")[0])
            cache.put("000001", name="平安银行", concepts=["银行", "金融科技"])
            hit, concepts = cache.lookup("000001")
            self.assertTrue(hit)
            self.assertEqual(concepts, ["银行", "金融科技"])

            cache2 = DailyConceptCache("2026-06-11", path=path)
            hit2, concepts2 = cache2.lookup("000001")
            self.assertTrue(hit2)
            self.assertEqual(concepts2, ["银行", "金融科技"])

    def test_fetched_at_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stock_concepts.json"
            cache = DailyConceptCache("2026-06-11", path=path)
            cache.put("000001", name="平安银行", concepts=["银行"])
            raw = path.read_text(encoding="utf-8")
            fetched_at = cache.lookup("000001")
            self.assertTrue(fetched_at[0])
            ts = json.loads(raw)["stocks"]["000001"]["fetched_at"]
            self.assertRegex(ts, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    def test_negative_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stock_concepts.json"
            cache = DailyConceptCache("2026-06-11", path=path)
            cache.put("600226", name="亨通股份", concepts=None)
            hit, concepts = cache.lookup("600226")
            self.assertTrue(hit)
            self.assertIsNone(concepts)


if __name__ == "__main__":
    unittest.main()
