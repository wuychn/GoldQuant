"""ML 校准 walk-forward 验证。"""

from __future__ import annotations

from typing import Any

import numpy as np

from quant.ml.dataset import ScoreSample
from quant.ml.optimizers import optimize_thresholds_grid


def _f1_at_threshold(samples: list[ScoreSample], watchlist_threshold: float) -> float:
    if not samples:
        return 0.0
    y = np.array([s.label for s in samples], dtype=float)
    totals = np.array([s.total for s in samples], dtype=float)
    pred = ((totals >= watchlist_threshold) & (y >= 0.5)).astype(float)
    if pred.sum() == 0:
        return 0.0
    precision = (pred * y).sum() / pred.sum()
    recall = (pred * y).sum() / max(y.sum(), 1)
    return float(2 * precision * recall / max(precision + recall, 1e-9))


def walk_forward_validate(
    samples: list[ScoreSample],
    *,
    base_thresholds: dict[str, float],
    min_train: int = 80,
    test_ratio: float = 0.2,
    min_test_f1: float = 0.25,
) -> dict[str, Any]:
    """时间序列切分：训练集优化阈值，测试集评估 F1。"""
    ordered = sorted(samples, key=lambda s: s.date)
    n = len(ordered)
    if n < min_train:
        return {
            "passed": False,
            "reason": f"样本 {n} 条，walk-forward 至少需要 {min_train} 条",
            "train_size": 0,
            "test_size": 0,
        }

    split = max(min_train, int(n * (1 - test_ratio)))
    if split >= n:
        split = n - max(1, int(n * test_ratio))
    train = ordered[:split]
    test = ordered[split:]
    if len(test) < 5:
        return {
            "passed": False,
            "reason": "测试集不足 5 条",
            "train_size": len(train),
            "test_size": len(test),
        }

    opt = optimize_thresholds_grid(train, base=base_thresholds)
    wt = float(opt["watchlist_threshold"])
    train_f1 = _f1_at_threshold(train, wt)
    test_f1 = _f1_at_threshold(test, wt)
    passed = test_f1 >= min_test_f1 and test_f1 >= train_f1 * 0.5

    return {
        "passed": passed,
        "train_size": len(train),
        "test_size": len(test),
        "train_f1": round(train_f1, 4),
        "test_f1": round(test_f1, 4),
        "min_test_f1": min_test_f1,
        "threshold_used": wt,
        "reason": "" if passed else f"测试集 F1={test_f1:.3f} 未达 {min_test_f1}",
    }
