"""观察池 nightly 更新测试。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from quant.store.observe_pool import update_observe_pool_evening


class ObservePoolTests(unittest.TestCase):
    def test_restore_when_score_passes(self) -> None:
        engine = MagicMock()
        ctx = MagicMock()
        score = SimpleNamespace(total=75.0)
        rows = [{"股票代码": "600487", "观察天数": 5}]
        remaining, restored, purged = update_observe_pool_evening(
            rows,
            ctx=ctx,
            engine=engine,
            score_by_code={"600487": score},
            enriched_by_code={},
            threshold=70,
            max_days=30,
        )
        self.assertEqual(remaining, [])
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0]["未达标连续天数"], 0)
        self.assertEqual(purged, [])

    def test_purge_after_max_days(self) -> None:
        engine = MagicMock()
        ctx = MagicMock()
        score = SimpleNamespace(total=55.0)
        rows = [{"股票代码": "600487", "观察天数": 29}]
        remaining, restored, purged = update_observe_pool_evening(
            rows,
            ctx=ctx,
            engine=engine,
            score_by_code={"600487": score},
            enriched_by_code={},
            threshold=70,
            max_days=30,
        )
        self.assertEqual(restored, [])
        self.assertEqual(remaining, [])
        self.assertEqual(len(purged), 1)
        self.assertEqual(purged[0]["观察天数"], 30)


if __name__ == "__main__":
    unittest.main()
