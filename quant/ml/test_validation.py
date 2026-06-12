"""walk-forward 验证测试。"""

from __future__ import annotations

import unittest

from quant.ml.dataset import ScoreSample
from quant.ml.validation import walk_forward_validate


def _sample(date: str, total: float, label: float) -> ScoreSample:
    return ScoreSample(
        date=date,
        code="600000",
        name="测试",
        total=total,
        dim_scores={"主升浪": total},
        label=label,
    )


class WalkForwardTests(unittest.TestCase):
    def test_insufficient_samples(self) -> None:
        samples = [_sample(f"2026-01-{i:02d}", 70 + i, 1.0) for i in range(1, 11)]
        r = walk_forward_validate(samples, base_thresholds={"watchlist_threshold": 65, "buy_threshold": 72, "sell_threshold": 45})
        self.assertFalse(r["passed"])

    def test_passes_with_enough_samples(self) -> None:
        samples = []
        for i in range(1, 101):
            label = 1.0 if i % 2 == 0 else 0.0
            total = 80.0 if label else 55.0
            samples.append(_sample(f"2026-01-{i % 28 + 1:02d}", total, label))
        r = walk_forward_validate(
            samples,
            base_thresholds={"watchlist_threshold": 65, "buy_threshold": 72, "sell_threshold": 45},
            min_train=80,
        )
        self.assertIn("test_f1", r)


if __name__ == "__main__":
    unittest.main()
