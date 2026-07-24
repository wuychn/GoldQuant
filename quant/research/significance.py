"""统计显著性：Bootstrap Sharpe、置换检验、FDR、Deflated Sharpe。"""

from __future__ import annotations

from typing import Any

import numpy as np


def bootstrap_sharpe(
    daily_returns: list[float],
    *,
    n_samples: int = 1000,
    seed: int = 42,
) -> dict[str, Any]:
    if len(daily_returns) < 5:
        return {"sharpe_mean": 0.0, "sharpe_ci_low": 0.0, "sharpe_ci_high": 0.0}
    rng = np.random.default_rng(seed)
    arr = np.array(daily_returns, dtype=float)
    sharpes = []
    for _ in range(n_samples):
        sample = rng.choice(arr, size=len(arr), replace=True)
        mean_r = sample.mean()
        std_r = sample.std(ddof=1)
        if std_r > 1e-9:
            sharpes.append(mean_r / std_r * np.sqrt(252))
        else:
            sharpes.append(0.0)
    sharpes.sort()
    lo = float(np.percentile(sharpes, 2.5))
    hi = float(np.percentile(sharpes, 97.5))
    return {
        "sharpe_mean": round(float(np.mean(sharpes)), 4),
        "sharpe_ci_low": round(lo, 4),
        "sharpe_ci_high": round(hi, 4),
    }


def permutation_test_sharpe(
    daily_returns: list[float],
    observed_sharpe: float,
    *,
    n_perm: int = 500,
    seed: int = 42,
) -> dict[str, Any]:
    if len(daily_returns) < 5:
        return {"p_value": 1.0, "significant_5pct": False}
    rng = np.random.default_rng(seed)
    arr = np.array(daily_returns, dtype=float)
    count = 0
    for _ in range(n_perm):
        # 符号翻转置换：检验均值是否显著为正
        signs = rng.choice([-1.0, 1.0], size=len(arr))
        perm = arr * signs
        std_r = perm.std(ddof=1)
        if std_r > 1e-9:
            s = perm.mean() / std_r * np.sqrt(252)
            if s >= observed_sharpe:
                count += 1
    p_val = count / n_perm
    return {"p_value": round(p_val, 4), "significant_5pct": p_val < 0.05}


def benjamini_hochberg(
    p_values: list[float],
    *,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Benjamini–Hochberg FDR 控制。"""
    n = len(p_values)
    if n == 0:
        return {"rejected": [], "adjusted_p": [], "alpha": alpha}
    order = np.argsort(p_values)
    ranked = np.array(p_values, dtype=float)[order]
    adj = np.empty(n, dtype=float)
    prev = 1.0
    for i in range(n - 1, -1, -1):
        rank = i + 1
        val = min(prev, ranked[i] * n / rank)
        adj[i] = val
        prev = val
    # 还原原始顺序
    adjusted = np.empty(n, dtype=float)
    adjusted[order] = adj
    rejected = [bool(p <= alpha) for p in adjusted]
    return {
        "rejected": rejected,
        "adjusted_p": [round(float(x), 6) for x in adjusted],
        "alpha": alpha,
        "n_tests": n,
        "n_rejected": sum(rejected),
    }


def deflated_sharpe_ratio(
    observed_sharpe: float,
    *,
    n_obs: int,
    n_trials: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> dict[str, Any]:
    """Bailey & López de Prado Deflated Sharpe Ratio（简化实现）。

    用于多重试错后判断观测 Sharpe 是否仍显著高于期望最大噪声 Sharpe。
    """
    if n_obs < 5 or n_trials < 1:
        return {"dsr": 0.0, "significant_5pct": False, "expected_max_sharpe": 0.0}

    # 无偏 Sharpe 方差近似
    sr = float(observed_sharpe)
    sr_var = (
        1.0
        - skew * sr
        + (kurtosis - 1.0) / 4.0 * sr * sr
    ) / max(n_obs - 1, 1)
    sr_std = float(np.sqrt(max(sr_var, 1e-12)))

    # 期望最大 |SR| under null ≈ sqrt(2 log n_trials) * sr_std（标准正态尾）
    # 更常用：E[max Z] ≈ (1-γ)*Φ^{-1}(1-1/N) + γ*Φ^{-1}(1-1/(N e))
    from scipy.stats import norm

    gamma = 0.5772156649
    if n_trials == 1:
        e_max_z = 0.0
    else:
        e_max_z = (1 - gamma) * norm.ppf(1 - 1 / n_trials) + gamma * norm.ppf(
            1 - 1 / (n_trials * np.e)
        )
    expected_max_sr = e_max_z * sr_std
    z = (sr - expected_max_sr) / sr_std if sr_std > 1e-12 else 0.0
    dsr = float(norm.cdf(z))
    return {
        "dsr": round(dsr, 4),
        "significant_5pct": dsr >= 0.95,
        "expected_max_sharpe": round(float(expected_max_sr), 4),
        "sr_std": round(sr_std, 4),
        "n_obs": n_obs,
        "n_trials": n_trials,
    }
