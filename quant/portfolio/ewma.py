"""EWMA 协方差（RiskMetrics 风格）。"""

from __future__ import annotations

import numpy as np
import pandas as pd


def estimate_covariance_ewma(
    daily: pd.DataFrame,
    codes: list[str],
    as_of: str,
    *,
    lookback: int = 60,
    lam: float = 0.94,
    ann: int = 252,
) -> tuple[dict[str, float], dict[str, dict[str, float]]] | None:
    """EWMA 协方差；λ 默认 0.94（日频 RiskMetrics）。"""
    if not codes:
        return None
    sub = daily[daily["code"].isin(codes) & (daily["date"] <= as_of)]
    if sub.empty:
        return None
    wide = (
        sub.sort_values("date")
        .pivot_table(index="date", columns="code", values="close", aggfunc="last")
        .tail(lookback + 1)
    )
    rets = wide.pct_change().dropna(how="all")
    rets = rets[[c for c in codes if c in rets.columns]].dropna()
    n_codes = rets.shape[1]
    if rets.shape[0] < max(20, n_codes + 5) or n_codes < 2:
        return None
    R = rets.to_numpy(dtype=float)
    t, n = R.shape
    # RiskMetrics：用样本协方差预热，避免从 0 冷启动系统性偏低
    warmup = min(max(20, n + 5), t)
    cov = np.cov(R[:warmup].T, ddof=1) if warmup >= 2 else np.zeros((n, n), dtype=float)
    if not np.all(np.isfinite(cov)):
        cov = np.zeros((n, n), dtype=float)
    for i in range(warmup, t):
        x = R[i : i + 1].T
        cov = lam * cov + (1 - lam) * (x @ x.T)
    cov *= ann
    sig = np.sqrt(np.clip(np.diag(cov), 1e-12, None))
    codes_present = list(rets.columns)
    sigmas = {c: float(sig[i]) for i, c in enumerate(codes_present)}
    covdict = {
        a: {b: float(cov[i, j]) for j, b in enumerate(codes_present)}
        for i, a in enumerate(codes_present)
    }
    return sigmas, covdict
