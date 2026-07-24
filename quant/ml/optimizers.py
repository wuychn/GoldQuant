"""阈值与维度权重的离线优化算法。

算法概览
--------
grid      穷举 (watchlist, buy) 阈值，按 research.ml_objective 最大化
linear    Ridge 回归：维度得分 → label，|coef| 归一化为权重；阈值仍用 grid
lightgbm  分类器特征重要性 → 权重；阈值仍用 grid
bayesian  scipy 差分进化在连续空间搜索阈值

sell_threshold：watchlist 样本无法评价卖出，搜索时固定为 base 值。
"""

from __future__ import annotations

from typing import Any

import numpy as np

from quant.ml.dataset import ScoreSample
from quant.ml.objective import score_threshold_objective


def _feature_matrix(samples: list[ScoreSample], dim_keys: list[str]) -> np.ndarray:
    """形状 (n_samples, n_dims)；缺失维度填 50（中性）。"""
    rows = []
    for s in samples:
        rows.append([s.dim_scores.get(k, 50.0) for k in dim_keys])
    return np.array(rows, dtype=float)


def _labels(samples: list[ScoreSample]) -> np.ndarray:
    return np.array([s.label for s in samples], dtype=float)


def _resolve_objective(objective: str | None) -> str:
    if objective:
        return objective
    from quant.config import load_quant_config

    return str((load_quant_config().get("research") or {}).get("ml_objective", "sharpe"))


def optimize_thresholds_grid(
    samples: list[ScoreSample],
    *,
    base: dict[str, float],
    objective: str | None = None,
) -> dict[str, Any]:
    """网格搜索阈值。

    sharpe/calmar：最大化 total>=wt 子集的按日组合目标；sell 固定 base。
    f1_legacy：最大化 F1（兼容旧行为）。
    """
    obj = _resolve_objective(objective)
    sell_fixed = float(base["sell_threshold"])
    best: dict[str, Any] = {
        "score": -1e18,
        "watchlist_threshold": base["watchlist_threshold"],
        "buy_threshold": base["buy_threshold"],
        "sell_threshold": sell_fixed,
        "objective": obj,
    }

    for wt in range(55, 86, 5):
        for bt in range(60, 91, 5):
            if not (sell_fixed < wt < bt):
                continue
            score = score_threshold_objective(
                samples, watchlist_threshold=float(wt), objective=obj
            )
            if score > best["score"]:
                best = {
                    "score": float(score),
                    "watchlist_threshold": float(wt),
                    "buy_threshold": float(bt),
                    "sell_threshold": sell_fixed,
                    "objective": obj,
                }
                if obj == "f1_legacy":
                    best["f1"] = float(score)
                else:
                    key = "portfolio_sharpe" if obj == "sharpe" else "portfolio_calmar"
                    best[key] = float(score)
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


def optimize_confirmation_intervals(
    samples: list[ScoreSample],
    *,
    base_cfg: dict,
) -> dict[str, Any]:
    """按市场状态搜索持续确认 persistence / 连续轮次。"""
    from quant.config import load_gates_config

    gates = load_gates_config()
    conf_base = gates.get("confirmation") or base_cfg.get("confirmation") or {}
    regimes = ("强势", "震荡", "弱势")
    out: dict[str, Any] = {
        "default_persistence_minutes": float(
            conf_base.get("default_persistence_minutes", 15)
        ),
        "default_min_consecutive_runs": int(
            conf_base.get("default_min_consecutive_runs", 2)
        ),
    }

    y = _labels(samples)
    totals = np.array([s.total for s in samples], dtype=float)
    if len(samples) < 10:
        for r in regimes:
            block = conf_base.get(r) or {}
            out[r] = {
                "persistence_minutes": float(block.get("persistence_minutes", 15)),
                "min_consecutive_runs": int(block.get("min_consecutive_runs", 2)),
                "max_window_minutes": float(block.get("max_window_minutes", 180)),
            }
        return out

    regime_grid = {
        "强势": [(10, 2), (15, 2), (20, 3)],
        "震荡": [(15, 2), (20, 3), (25, 3)],
        "弱势": [(25, 3), (30, 3), (35, 4)],
    }
    for regime in regimes:
        best_f1 = -1.0
        best_pair = (20.0, 3)
        for persist, runs in regime_grid.get(regime, [(20, 3)]):
            wt = (
                float(conf_base.get("watchlist_threshold", 65))
                if isinstance(conf_base, dict)
                else 65
            )
            pred = (totals >= wt).astype(int)
            from sklearn.metrics import f1_score

            f1 = f1_score(y, pred, zero_division=0)
            if f1 > best_f1:
                best_f1 = f1
                best_pair = (float(persist), int(runs))
        out[regime] = {
            "persistence_minutes": best_pair[0],
            "min_consecutive_runs": best_pair[1],
            "max_window_minutes": float(
                (conf_base.get(regime) or {}).get("max_window_minutes", 180)
            ),
        }
    return out


def optimize_concept_score_weights(
    samples: list[ScoreSample],
    *,
    base_weights: dict[str, float],
) -> dict[str, Any]:
    """基于 concept_theme 维度与 label 相关性，微调概念指标权重组合。"""
    keys = ("selection_count", "composite_gain", "net_fund_flow")
    base = {k: float(base_weights.get(k, 0)) for k in keys}
    if sum(base.values()) <= 0:
        base = {"selection_count": 50.0, "composite_gain": 30.0, "net_fund_flow": 20.0}

    if len(samples) < 10:
        return {"score_weights": base, "note": "样本不足，保留原 concept 指标权重"}

    ct = np.array([s.dim_scores.get("concept_theme", 50.0) for s in samples], dtype=float)
    y = _labels(samples)
    if ct.std() <= 1e-6:
        return {"score_weights": base, "note": "concept_theme 得分无方差，保留原权重"}

    corr = float(np.corrcoef(ct, y)[0, 1])
    presets = [
        (50, 30, 20),
        (45, 35, 20),
        (40, 35, 25),
        (55, 25, 20),
        (45, 30, 25),
        (50, 25, 25),
    ]
    best_preset = base
    best_score = -1.0
    for sc, cg, nf in presets:
        w_sum = sc + cg + nf
        proxy = ct * (w_sum / 100.0)
        pred = (proxy >= np.median(proxy)).astype(float)
        precision = (pred * y).sum() / max(pred.sum(), 1)
        recall = (pred * y).sum() / max(y.sum(), 1)
        f1 = 2 * precision * recall / max(precision + recall, 1e-9)
        if f1 > best_score:
            best_score = f1
            best_preset = {
                "selection_count": float(sc),
                "composite_gain": float(cg),
                "net_fund_flow": float(nf),
            }

    if corr > 0.08:
        best_preset["net_fund_flow"] = min(35.0, best_preset["net_fund_flow"] + 3.0)
        best_preset["selection_count"] = max(40.0, best_preset["selection_count"] - 3.0)
    elif corr < 0.0:
        best_preset["selection_count"] = min(60.0, best_preset["selection_count"] + 3.0)
        best_preset["net_fund_flow"] = max(10.0, best_preset["net_fund_flow"] - 3.0)

    total = sum(best_preset.values())
    if total > 0:
        scale = 100.0 / total
        best_preset = {k: round(v * scale, 1) for k, v in best_preset.items()}

    return {
        "score_weights": best_preset,
        "concept_theme_corr": corr,
        "proxy_f1": best_score,
    }


def optimize_bayesian(
    samples: list[ScoreSample],
    *,
    base: dict[str, float],
    objective: str | None = None,
) -> dict[str, Any]:
    """差分进化搜索 (wt, bt)；sell 固定；目标由 ml_objective 决定。"""
    from scipy.optimize import differential_evolution

    obj = _resolve_objective(objective)
    if len(samples) < 10:
        return optimize_thresholds_grid(samples, base=base, objective=obj)

    sell_fixed = float(base["sell_threshold"])

    def loss(params: np.ndarray) -> float:
        wt, bt = params
        if not (sell_fixed < wt < bt <= 95):
            return 1e9
        score = score_threshold_objective(
            samples, watchlist_threshold=float(wt), objective=obj
        )
        return -score

    bounds = [(55, 85), (65, 92)]
    res = differential_evolution(loss, bounds, seed=42, maxiter=40, polish=True)
    wt, bt = res.x
    if not (sell_fixed < wt < bt):
        bt = max(wt + 1.0, float(base["buy_threshold"]))
    score = score_threshold_objective(
        samples, watchlist_threshold=float(wt), objective=obj
    )
    out: dict[str, Any] = {
        "watchlist_threshold": round(float(wt), 2),
        "buy_threshold": round(float(bt), 2),
        "sell_threshold": sell_fixed,
        "score": float(score),
        "objective": obj,
        "method_detail": "scipy differential_evolution",
    }
    if obj == "f1_legacy":
        out["f1"] = float(score)
    return out
