"""store.write_daily_raw 并发写不损坏分区。"""

from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from quant.data import store as st


class ConcurrentWriteDailyRawTests(unittest.TestCase):
    def test_parallel_writes_same_year_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            from quant.store.paths import override_quant_home

            with override_quant_home(root):
                def _one(i: int) -> None:
                    code = f"{i:06d}"
                    df = pd.DataFrame(
                        {
                            "code": [code],
                            "date": ["2024-01-02"],
                            "open": [1.0],
                            "high": [1.0],
                            "low": [1.0],
                            "close": [1.0],
                            "volume": [100],
                            "amount": [100.0],
                        }
                    )
                    st.write_daily_raw(df)

                with ThreadPoolExecutor(max_workers=8) as ex:
                    list(ex.map(_one, range(40)))

                out = st.read_daily_raw(start="2024-01-01", end="2024-12-31")
                self.assertEqual(len(out), 40)
                self.assertEqual(out["code"].nunique(), 40)
                # 分区可再次追加
                st.write_daily_raw(
                    pd.DataFrame(
                        {
                            "code": ["000001"],
                            "date": ["2024-01-03"],
                            "close": [2.0],
                        }
                    )
                )
                out2 = st.read_daily_raw()
                self.assertGreaterEqual(len(out2), 41)


if __name__ == "__main__":
    unittest.main()
