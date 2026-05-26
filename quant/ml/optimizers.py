"""阈值与维度权重的离线优化算法。

算法概览
--------
grid      穷举 (watchlist, buy, sell) 阈值组合，最大化 F1
linear    Ridge 回归：维度得分 → label，|coef| 归一化为权重；阈值仍用 grid
lightgbm  分类器特征重要性 → 权重；阈值仍用 grid
bayesian  scipy 差分进化在连续空间搜索阈值，最大化 F1

注意：sell_threshold 在 grid 中参与搜索，但当前 optimizers 的 F1 主要按
「总分过 watchlist 线且 label 为正」评估；sell 阈值由 grid 约束 st < wt < bt 一并输出。
"""

from __future__ import annotations

from typing import Any

import numpy as np

from quant.ml.dataset import ScoreSample


def _feature_matrix(samples: list[ScoreSample], dim_keys: list[str]) -> np.ndarray:
    """形状 (n_samples, n_dims)；缺失维度填 50（中性）。"""
    rows = []
    for s in samples:
        rows.append([s.dim_scores.get(k, 50.0) for k in dim_keys])
    return np.array(rows, dtype=float)


def _labels(samples: list[ScoreSample]) -> np.ndarray:
    return np.array([s.label for s in samples], dtype=float)


def optimize_thresholds_grid(
    samples: list[ScoreSample],
    *,
    base: dict[str, float],
) -> dict[str, Any]:
    """网格搜索三阈值，目标最大化 F1。

    预测规则（简化）：total >= watchlist_threshold 且 label>=0.5 视为「选对」。
    """
    y = _labels(samples)
    totals = np.array([s.total for s in samples], dtype=float)
    best = {
        "score": -1.0,
        "watchlist_threshold": base["watchlist_threshold"],
        "buy_threshold": base["buy_threshold"],
        "sell_threshold": base["sell_threshold"],
    }

    for wt in range(55, 86, 5):
        for bt in range(60, 91, 5):
            for st in range(30, 56, 5):
                if not (st < wt < bt):
                    continue
                pred = ((totals >= wt) & (y >= 0.5)).astype(float)
                if pred.sum() == 0:
                    continue
                precision = (pred * y).sum() / pred.sum()
                recall = (pred * y).sum() / max(y.sum(), 1)
                f1 = 2 * precision * recall / max(precision + recall, 1e-9)
                if f1 > best["score"]:
                    best = {
                        "score": float(f1),
                        "watchlist_threshold": float(wt),
                        "buy_threshold": float(bt),
                        "sell_threshold": float(st),
                        "precision": float(precision),
                        "recall": float(recall),
                        "f1": float(f1),
                    }
    return best


def optimize_weights_linear(
    samples: list[ScoreSample],
    *,
    dim_keys: list[str],
    base_weights: dict[str, float],
) -> dict[str, Any]:
    """Ridge 回归：y=label，X=各维度得分；|coef| 归一化到权重和 100。"""
    from sklearn.linear_model import Ridge

    if len(samples) < 10:
        return {"weights": base_weights, "note": "样本不足，保留原权重"}

    X = _feature_matrix(samples, dim_keys)
    y = _labels(samples)
    model = Ridge(alpha=1.0)
    model.fit(X, y)
    coef = np.abs(model.coef_)
    if coef.sum() <= 0:
        return {"weights": base_weights, "note": "线性回归系数无效，保留原权重"}
    scale = 100.0 / coef.sum()
    weights = {k: round(float(c * scale), 2) for k, c in zip(dim_keys, coef)}
    return {"weights": weights, "coef": model.coef_.tolist(), "intercept": float(model.intercept_)}


def optimize_weights_lightgbm(
    samples: list[ScoreSample],
    *,
    dim_keys: list[str],
    base_weights: dict[str, float],
) -> dict[str, Any]:
    """LightGBM 二分类 + feature_importances_ 归一化为权重。"""
    try:
        import lightgbm as lgb
    except ImportError as e:
        return {"weights": base_weights, "note": f"未安装 lightgbm: {e}"}

    if len(samples) < 20:
        return {"weights": base_weights, "note": "样本不足，保留原权重"}

    X = _feature_matrix(samples, dim_keys)
    y = _labels(samples)
    model = lgb.LGBMClassifier(
        n_estimators=80,
        max_depth=4,
        learning_rate=0.08,
        verbose=-1,
    )
    model.fit(X, y)
    imp = model.feature_importances_
    if imp.sum() <= 0:
        return {"weights": base_weights, "note": "LightGBM 特征重要性为 0"}
    scale = 100.0 / imp.sum()
    weights = {k: round(float(v * scale), 2) for k, v in zip(dim_keys, imp)}
    return {"weights": weights, "importance": imp.tolist()}


def optimize_bayesian(
    samples: list[ScoreSample],
    *,
    base: dict[str, float],
) -> dict[str, Any]:
    """差分进化在连续空间搜索 (wt, bt, st)，最小化 -F1。"""
    from scipy.optimize import differential_evolution
    from sklearn.metrics import f1_score

    if len(samples) < 10:
        return optimize_thresholds_grid(samples, base=base)

    totals = np.array([s.total for s in samples], dtype=float)
    y = _labels(samples)

    def objective(params: np.ndarray) -> float:
        wt, bt, st = params
        if not (30 <= st < wt < bt <= 95):
            return 1.0
        pred = ((totals >= wt) & (totals >= bt * 0.98)).astype(int)
        return -f1_score(y, pred, zero_division=0)

    bounds = [(55, 85), (65, 92), (30, 55)]
    res = differential_evolution(objective, bounds, seed=42, maxiter=40, polish=True)
    wt, bt, st = res.x
    pred = ((totals >= wt) & (totals >= bt * 0.98)).astype(int)
    return {
        "watchlist_threshold": round(float(wt), 2),
        "buy_threshold": round(float(bt), 2),
        "sell_threshold": round(float(st), 2),
        "f1": float(f1_score(y, pred, zero_division=0)),
        "method_detail": "scipy differential_evolution",
    }
