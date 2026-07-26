"""Deflated Sharpe Ratio（DSR）：对多重检验校正后的 Sharpe 显著性。

参考 Bailey & López de Prado (2014)。给定 N 次试验中观察到的最大 Sharpe，
DSR 给出「该 Sharpe 在零效应下仍为正」的概率。

参数：
- sharpe：观察到的年化 Sharpe
- n：样本数（日数）
- n_trials：试验次数（参数组合数 / 因子数）
- skew / kurt：收益分布的偏度/峰度（缺省用正态近似）
- t：年化因子（252）
"""

from __future__ import annotations

import math


def _norm_cdf(z: float) -> float:
    """标准正态 CDF，用 math.erf。"""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _expected_max_sharpe(n_trials: int, var_sharpe: float) -> float:
    """E[max Z] over n_trials 标准正态。"""
    if n_trials <= 1:
        return 0.0
    e = 0.0
    for i in range(1, n_trials):
        e += 1.0 / i
    # 用 Euler-Mascheroni 近似
    return math.sqrt(var_sharpe) * (e - 0.5772156649) if n_trials > 50 else math.sqrt(var_sharpe) * sum(1.0 / i for i in range(1, n_trials))


def deflated_sharpe(
    sharpe: float,
    *,
    n: int,
    n_trials: int = 1,
    skew: float = 0.0,
    kurt: float = 3.0,
    annualization: int = 252,
) -> dict[str, float]:
    """返回 {'dsr': p_value, 'adjusted_sharpe': ...}。

    DSR 越接近 1，越说明该 Sharpe 经多重检验校正后仍显著为正。
    """
    if n < 2:
        return {"dsr": 0.5, "adjusted_sharpe": 0.0, "significant": False}
    # Sharpe 方差（考虑偏度峰度）
    gamma = skew * annualization ** 0.5 * sharpe ** 2
    var_sh = (1 - skew * sharpe * math.sqrt(1 / annualization) + (kurt - 1) / 4 * sharpe ** 2) / (n - 1)
    var_sh = max(var_sh, 1e-12)
    e_max = _expected_max_sharpe(n_trials, var_sh)
    # 检验 sharpe > e_max
    sd = math.sqrt(var_sh)
    z = (sharpe - e_max) / sd if sd > 1e-12 else 0.0
    # DSR = P(SR <= observed | null) = Φ(z)；越大越显著
    dsr = float(_norm_cdf(z))
    return {
        "dsr": round(dsr, 4),  # >0.95 表示经多重检验校正后仍显著为正
        "adjusted_sharpe": round(sharpe - e_max, 4),
        "z": round(z, 3),
        "significant": dsr > 0.95,
    }
