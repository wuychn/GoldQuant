"""敏感性扫描：sens-workers=1 与 N 结果一致。"""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from quant.backtest.engine import run_backtest
from quant.backtest.metrics import compute_metrics
from quant.backtest.sensitivity_report import run_backtest_sensitivity
from quant.portfolio.target import TargetPortfolio


def _tiny_daily(n_days: int = 40) -> tuple[pd.DataFrame, list[str], dict[str, dict[str, float]]]:
    dates = list(pd.bdate_range(start="2024-01-02", periods=n_days).strftime("%Y-%m-%d"))
    rows = []
    rng = np.random.default_rng(11)
    codes = ["000001", "000002", "000003"]
    for code in codes:
        p = 10.0
        for d in dates:
            p = max(p * (1 + float(rng.normal(0.0, 0.01))), 1.0)
            rows.append(
                {
                    "code": code,
                    "date": d,
                    "open": p,
                    "high": p * 1.01,
                    "low": p * 0.99,
                    "close": p,
                    "volume": 1e6,
                    "amount": p * 1e6,
                    "float_mv": 1e10,
                }
            )
    daily = pd.DataFrame(rows)
    # 简单稳定 alpha：按 code 固定分
    alpha_by_date = {
        d: {"000001": 1.0, "000002": 0.5, "000003": 0.2} for d in dates
    }
    return daily, dates, alpha_by_date


class SensitivityParallelTests(unittest.TestCase):
    def test_serial_vs_parallel_ctx(self):
        daily, dates, alpha_by_date = _tiny_daily()
        grids = {
            "n_enter": [2.0, 3.0],
            "target_vol": [0.12, 0.15],
        }
        defaults = {
            "n_enter": 2.0,
            "n_exit": 5.0,
            "target_vol": 0.15,
            "buffer_abs": 0.01,
            "sector_cap": 0.5,
        }

        def alpha_fn(d: str, _rows: dict) -> dict[str, float]:
            return alpha_by_date.get(d, {})

        def base_run(params: dict[str, float]) -> float:
            ne = int(params.get("n_enter", 2))
            tv = float(params.get("target_vol", 0.15))
            pol = TargetPortfolio.from_config(
                n_enter=ne,
                n_exit=max(ne + 2, 5),
                max_stocks=3,
                target_vol=tv,
                buffer_abs=0.01,
                sector_cap=0.5,
                daily=daily,
            )
            b = run_backtest(
                daily=daily,
                dates=dates,
                alpha_fn=alpha_fn,
                policy=pol,
                max_positions=3,
                exit_config=None,
                strict_signals=True,
            )
            return float(compute_metrics(b).get("sharpe") or 0.0)

        s1 = run_backtest_sensitivity(base_run_fn=base_run, grids=grids, workers=1)
        s2 = run_backtest_sensitivity(
            workers=2,
            grids=grids,
            parallel_ctx={
                "daily": daily,
                "dates": dates,
                "alpha_by_date": alpha_by_date,
                "max_positions": 3,
                "strict_signals": True,
                "use_exit": False,
                "defaults": defaults,
            },
        )
        for param in grids:
            self.assertEqual(s1[param]["grid"], s2[param]["grid"])
            for a, b in zip(s1[param]["sharpes"], s2[param]["sharpes"]):
                if np.isnan(a) and np.isnan(b):
                    continue
                self.assertAlmostEqual(float(a), float(b), places=8, msg=param)


if __name__ == "__main__":
    unittest.main()
