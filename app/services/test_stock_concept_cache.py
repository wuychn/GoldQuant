"""个股概念周缓存单元测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from app.services.stock_concept_cache import StockConceptCache
from quant.timeutil import cn_datetime_str, cn_now


class StockConceptCacheTests(unittest.TestCase):
    def test_put_and_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stock_concepts.json"
            cache = StockConceptCache(path=path)
            self.assertFalse(cache.lookup("000001")[0])
            cache.put("000001", name="平安银行", concepts=["银行", "金融科技"])
            hit, concepts = cache.lookup("000001")
            self.assertTrue(hit)
            self.assertEqual(concepts, ["银行", "金融科技"])

            cache2 = StockConceptCache(path=path)
            hit2, concepts2 = cache2.lookup("000001")
            self.assertTrue(hit2)
            self.assertEqual(concepts2, ["银行", "金融科技"])

    def test_fetched_at_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stock_concepts.json"
            cache = StockConceptCache(path=path)
            cache.put("000001", name="平安银行", concepts=["银行"])
            raw = path.read_text(encoding="utf-8")
            fetched_at = cache.lookup("000001")
            self.assertTrue(fetched_at[0])
            ts = json.loads(raw)["stocks"]["000001"]["fetched_at"]
            self.assertRegex(ts, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    def test_negative_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stock_concepts.json"
            cache = StockConceptCache(path=path)
            cache.put("600226", name="亨通股份", concepts=None)
            hit, concepts = cache.lookup("600226")
            self.assertTrue(hit)
            self.assertIsNone(concepts)

    def test_put_with_fit_ranks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stock_concepts.json"
            cache = StockConceptCache(path=path)
            fit = [{"rank": 1, "concept": "超级电容"}, {"rank": 2, "concept": "储能"}]
            cache.put(
                "000636",
                name="风华高科",
                concepts=["超级电容", "储能"],
                source="同花顺F10粘合度",
                fit_ranks=fit,
            )
            hit, fit_out, concepts = cache.lookup_fit("000636")
            self.assertTrue(hit)
            self.assertEqual(fit_out, fit)
            self.assertEqual(concepts, ["超级电容", "储能"])

    def test_stale_entry_is_miss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stock_concepts.json"
            cache = StockConceptCache(path=path, ttl_days=7)
            stale_at = cn_datetime_str(cn_now() - timedelta(days=8))
            path.write_text(
                json.dumps(
                    {
                        "stocks": {
                            "000001": {
                                "股票名称": "平安银行",
                                "所属概念": ["银行"],
                                "概念来源": "问财",
                                "fetched_at": stale_at,
                            }
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self.assertFalse(cache.lookup("000001")[0])

    def test_wencai_put_preserves_fit_ranks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stock_concepts.json"
            cache = StockConceptCache(path=path)
            fit = [{"rank": 1, "concept": "存储芯片"}]
            cache.put(
                "000636",
                name="风华高科",
                concepts=["存储芯片"],
                source="同花顺F10粘合度",
                fit_ranks=fit,
            )
            cache.put("000636", name="风华高科", concepts=["存储芯片", "半导体"], source="问财")
            hit, fit_out, concepts = cache.lookup_fit("000636")
            self.assertTrue(hit)
            self.assertEqual(fit_out, fit)
            self.assertEqual(concepts, ["存储芯片", "半导体"])


if __name__ == "__main__":
    unittest.main()
