"""build_panel / build_alpha_by_date：workers=1 与 N 结果一致。"""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from quant.factors.alpha_builder import build_alpha_by_date
from quant.factors.panel_builder import build_panel, _split_codes


def _synth_daily(n_codes: int = 4, periods: int = 60, seed: int = 7) -> tuple[pd.DataFrame, list[str]]:
    rng = np.random.default_rng(seed)
    dates = list(pd.bdate_range(end="2024-06-28", periods=periods).strftime("%Y-%m-%d"))
    rows = []
    for i in range(n_codes):
        code = f"{i:06d}"
        p = 10.0 + i
        for d in dates:
            p = max(p * (1 + float(rng.normal(0.001, 0.02))), 1.0)
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
                    "turnover_rate": 1.0,
                    "float_mv": 1e10 * (i + 1),
                    "name": code,
                }
            )
    return pd.DataFrame(rows), dates


def _panel_key(r) -> tuple:
    raw_items = tuple(sorted((k, round(float(v), 10)) for k, v in r.raw.items()))
    neut_items = tuple(sorted((k, round(float(v), 10)) for k, v in r.neutral.items()))
    fwd = None if r.forward_return_pct is None else round(float(r.forward_return_pct), 10)
    meta_fwd = r.meta.get("fwd") or {}
    meta_items = tuple(sorted((int(k), round(float(v), 10)) for k, v in meta_fwd.items()))
    return (r.date, r.code, r.industry, r.log_mcap, raw_items, neut_items, fwd, meta_items)


class PanelParallelTests(unittest.TestCase):
    def test_split_codes_covers_all(self):
        codes = [f"{i:06d}" for i in range(10)]
        parts = _split_codes(codes, 3)
        self.assertEqual(sorted(sum(parts, [])), codes)
        self.assertEqual(len(parts), 3)

    def test_workers_match_raw_neutral_fwd(self):
        daily, dates = _synth_daily()
        eval_dates = dates[-5:-2]
        universe = {d: sorted(daily["code"].unique()) for d in eval_dates}
        industries = {c: "银行" if int(c) % 2 == 0 else "医药" for c in daily["code"].unique()}

        p1 = build_panel(
            eval_dates,
            daily=daily,
            industries=industries,
            universe_by_date=universe,
            workers=1,
        )
        p2 = build_panel(
            eval_dates,
            daily=daily,
            industries=industries,
            universe_by_date=universe,
            workers=2,
        )
        p4 = build_panel(
            eval_dates,
            daily=daily,
            industries=industries,
            universe_by_date=universe,
            workers=4,
        )
        self.assertTrue(p1)
        self.assertEqual(len(p1), len(p2))
        self.assertEqual(len(p1), len(p4))
        self.assertEqual(
            sorted(_panel_key(r) for r in p1),
            sorted(_panel_key(r) for r in p2),
        )
        self.assertEqual(
            sorted(_panel_key(r) for r in p1),
            sorted(_panel_key(r) for r in p4),
        )

    def test_alpha_by_date_workers_match(self):
        daily, dates = _synth_daily(n_codes=3, periods=50)
        eval_dates = dates[-4:-1]
        # build_alpha_by_date → build_panel 会读 universe；注入需改 build_panel 调用。
        # 这里直接对比 build_panel 合成路径：用 monkeypatch universe。
        from unittest.mock import patch

        uni = {d: sorted(daily["code"].unique()) for d in eval_dates}
        with patch(
            "quant.factors.panel_builder._resolve_universe",
            return_value={d: set(uni[d]) for d in eval_dates},
        ):
            a1 = build_alpha_by_date(eval_dates, daily, use_ic_weights=False, workers=1)
            a2 = build_alpha_by_date(eval_dates, daily, use_ic_weights=False, workers=2)
        self.assertEqual(set(a1), set(a2))
        for d in a1:
            self.assertEqual(set(a1[d]), set(a2[d]))
            for c in a1[d]:
                self.assertAlmostEqual(a1[d][c], a2[d][c], places=10)


def _scan_param_fn(v: float) -> float:
    """模块级，供 ProcessPool pickle。"""
    return float(v) * 2.0 + 0.5


class ScanParamParallelTests(unittest.TestCase):
    def test_scan_param_workers_match(self):
        from quant.research.sensitivity import scan_param

        r1 = scan_param("x", [1.0, 2.0, 3.0, 4.0], run_fn=_scan_param_fn, workers=1)
        r2 = scan_param("x", [1.0, 2.0, 3.0, 4.0], run_fn=_scan_param_fn, workers=2)
        self.assertEqual(r1.sharpes, r2.sharpes)
        self.assertEqual(r1.stability, r2.stability)


if __name__ == "__main__":
    unittest.main()
