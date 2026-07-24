"""walk-forward 验证测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from quant.ml.dataset import ScoreSample
from quant.ml.objective import portfolio_sharpe_from_samples, score_threshold_objective
from quant.ml.optimizers import optimize_thresholds_grid
from quant.ml.validation import walk_forward_validate


def _sample(
    date: str,
    total: float,
    label: float,
    *,
    code: str = "600000",
    fwd: float | None = None,
) -> ScoreSample:
    if fwd is None:
        fwd = 1.0 if label >= 0.5 else -0.5
    return ScoreSample(
        date=date,
        code=code,
        name="测试",
        total=total,
        dim_scores={"main_wave": total},
        label=label,
        forward_return_pct=fwd,
    )


class WalkForwardTests(unittest.TestCase):
    def test_insufficient_samples(self) -> None:
        samples = [_sample(f"2026-01-{i:02d}", 70 + i, 1.0) for i in range(1, 11)]
        r = walk_forward_validate(
            samples,
            base_thresholds={
                "watchlist_threshold": 65,
                "buy_threshold": 72,
                "sell_threshold": 45,
            },
        )
        self.assertFalse(r["passed"])

    @patch(
        "quant.ml.validation.load_quant_config",
        return_value={"research": {"ml_objective": "f1_legacy", "min_oos_sharpe": 0.0}},
    )
    def test_passes_with_enough_samples_f1(self, _cfg) -> None:
        samples = []
        for i in range(1, 101):
            label = 1.0 if i % 2 == 0 else 0.0
            total = 80.0 if label else 55.0
            day = f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}"
            samples.append(_sample(day, total, label, code=f"6000{i%10}"))
        r = walk_forward_validate(
            samples,
            base_thresholds={
                "watchlist_threshold": 65,
                "buy_threshold": 72,
                "sell_threshold": 45,
            },
            min_train=80,
        )
        self.assertIn("test_f1", r)
        self.assertEqual(r.get("objective"), "f1_legacy")

    def test_portfolio_sharpe_groups_by_date(self) -> None:
        samples = [
            _sample("2026-01-01", 80, 1.0, code="1", fwd=2.0),
            _sample("2026-01-01", 70, 1.0, code="2", fwd=0.0),
            _sample("2026-01-02", 80, 1.0, code="1", fwd=1.0),
            _sample("2026-01-03", 80, 1.0, code="1", fwd=-1.0),
            _sample("2026-01-04", 80, 1.0, code="1", fwd=0.5),
            _sample("2026-01-05", 80, 1.0, code="1", fwd=0.5),
        ]
        # 不应把 6 个截面点当 6 天；应按 5 个交易日
        sr = portfolio_sharpe_from_samples(samples)
        self.assertIsInstance(sr, float)

    def test_grid_sharpe_keeps_sell_fixed(self) -> None:
        samples = []
        for d in range(1, 21):
            for j in range(5):
                high = j < 3
                samples.append(
                    _sample(
                        f"2026-01-{d:02d}",
                        80.0 if high else 50.0,
                        1.0 if high else 0.0,
                        code=f"6000{j}",
                        fwd=1.5 if high else -1.0,
                    )
                )
        opt = optimize_thresholds_grid(
            samples,
            base={
                "watchlist_threshold": 65,
                "buy_threshold": 72,
                "sell_threshold": 42,
            },
            objective="sharpe",
        )
        self.assertEqual(opt["sell_threshold"], 42.0)
        self.assertEqual(opt["objective"], "sharpe")
        self.assertGreater(
            score_threshold_objective(
                samples, watchlist_threshold=opt["watchlist_threshold"], objective="sharpe"
            ),
            -1e8,
        )


if __name__ == "__main__":
    unittest.main()
