"""Deflated Sharpe Ratio（DSR）：对多重检验校正后的 Sharpe 显著性。

参考 Bailey & López de Prado (2014)《The Deflated Sharpe Ratio》。
给定 N 次试验中观察到的 Sharpe，DSR 给出「该 Sharpe 在零效应下仍为正」的置信度。

实现说明：用 math.erf/erfc 提供 Φ / Φ⁻¹，避免 scipy 依赖。
"""

from __future__ import annotations

import math

_EULER_GAMMA = 0.5772156649
_SQRT2 = math.sqrt(2.0)


def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / _SQRT2))


def _norm_ppf(p: float) -> float:
    """标准正态分位数反函数（Acklam 近似，精度足够 DSR 用途）。"""
    if p <= 0.0:
        return -float("inf")
    if p >= 1.0:
        return float("inf")
    a = [
        -3.969683028665376e01, 2.209460983245205e02, -2.759285104469687e02,
        1.383577518672690e02, -3.066479806617146e01, 2.506628277453239e00,
    ]
    b = [
        -5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
        6.680131188771972e01, -1.328068155288574e01,
    ]
    c = [
        -7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
        -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00,
    ]
    d = [
        7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
        3.754408661907416e00,
    ]
    plow = 0.02425
    phigh = 1.0 - plow
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1))
    if p <= phigh:
        q = p - 0.5
        r = q * q
        return ((((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q) / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1))


def _expected_max_sharpe_z(n_trials: int) -> float:
    """E[max Z] under null（标准正态尾），Bailey 标准形式。

    E[max Z] ≈ (1-γ)*Φ⁻¹(1-1/N) + γ*Φ⁻¹(1-1/(N·e))
    """
    if n_trials <= 1:
        return 0.0
    return (1.0 - _EULER_GAMMA) * _norm_ppf(1.0 - 1.0 / n_trials) + _EULER_GAMMA * _norm_ppf(1.0 - 1.0 / (n_trials * math.e))


def deflated_sharpe(
    sharpe: float,
    *,
    n: int,
    n_trials: int = 1,
    skew: float = 0.0,
    kurt: float = 3.0,
    annualization: int = 252,
) -> dict[str, float]:
    """返回 DSR 与诊断量。

    DSR = Φ((SR - E[max SR]) / std(SR))；>0.95 表示经多重检验校正后仍显著为正。
    """
    if n < 2:
        return {"dsr": 0.5, "adjusted_sharpe": 0.0, "z": 0.0, "significant": False,
                "expected_max_sharpe": 0.0, "sr_std": 0.0}
    sr = float(sharpe)
    # 无偏 Sharpe 方差（考虑偏度峰度）
    sr_var = (1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr) / max(n - 1, 1)
    sr_var = max(sr_var, 1e-12)
    sr_std = math.sqrt(sr_var)
    e_max_z = _expected_max_sharpe_z(n_trials)
    expected_max_sr = e_max_z * sr_std
    z = (sr - expected_max_sr) / sr_std if sr_std > 1e-12 else 0.0
    dsr = float(_norm_cdf(z))
    return {
        "dsr": round(dsr, 4),
        "adjusted_sharpe": round(sr - expected_max_sr, 4),
        "z": round(z, 3),
        "significant": dsr > 0.95,
        "expected_max_sharpe": round(float(expected_max_sr), 4),
        "sr_std": round(float(sr_std), 4),
    }
