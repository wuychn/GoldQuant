"""波动率目标：权重 ∝ 1/实现波动率，再缩放到目标组合波动率。

替代离散 regime 切换：用波动率倒数加权天然给高波动票小仓位，
再用 scalar 把组合年化波动率拉到 target_vol（如 15%）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def realized_vol(daily: pd.DataFrame, code: str, as_of: str, lookback: int = 20, ann: int = 252) -> float | None:
    sub = daily[(daily["code"] == code) & (daily["date"] <= as_of)].sort_values("date")
    if len(sub) < lookback + 1:
        return None
    closes = pd.to_numeric(sub["close"], errors="coerce").iloc[-(lookback + 1):]
    rets = closes.pct_change().dropna()
    if len(rets) < 2:
        return None
    return float(rets.std(ddof=1) * np.sqrt(ann))


def inv_vol_weights(vols: dict[str, float], *, max_weight: float = 0.20, min_weight: float = 0.0) -> dict[str, float]:
    """1/波动率倒数加权，单票封顶 max_weight，归一化。"""
    inv = {c: 1.0 / max(v, 1e-6) for c, v in vols.items() if v and v > 0}
    if not inv:
        return {}
    total = sum(inv.values())
    w = {c: v / total for c, v in inv.items()}
    # 封顶再归一化（迭代 3 次）
    for _ in range(3):
        over = {c: wt for c, wt in w.items() if wt > max_weight}
        if not over:
            break
        excess = sum(wt - max_weight for wt in over.values())
        for c in over:
            w[c] = max_weight
        under = [c for c in w if w[c] < max_weight]
        if under:
            for c in under:
                w[c] += excess / len(under)
    return {c: max(min_weight, wt) for c, wt in w.items()}


def estimate_covariance(
    daily: pd.DataFrame,
    codes: list[str],
    as_of: str,
    *,
    lookback: int = 60,
    ann: int = 252,
    shrink_delta: float | None = None,
) -> tuple[dict[str, float], dict[str, dict[str, float]]] | None:
    """估年化波动率向量 + 收缩协方差矩阵（dict-of-dict）。

    用 lookback 日收益构造 T×N 矩阵（listwise 删缺失），样本协方差向
    **恒定相关**目标收缩（Ledoit-Wolf 常数相关目标的简化）：
      Σ_shrink = δ·Σ_target + (1-δ)·Σ_sample
    δ 默认按样本规模启发式（可显式覆盖）。返回 (sigmas, cov) 或 None（数据不足）。
    取代此前 scale_to_target_vol 的单一 ρ=0.3：同板块高相关、跨板块低相关得以体现。
    """
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
    codes_present = list(rets.columns)
    sample = np.cov(R, rowvar=False, ddof=1) * ann
    sig = np.sqrt(np.clip(np.diag(sample), 1e-12, None))
    # 恒定相关目标：用平均非对角相关重建
    corr = sample / np.outer(sig, sig)
    np.fill_diagonal(corr, 1.0)
    off = corr[~np.eye(n_codes, dtype=bool)]
    rho = float(np.mean(off)) if off.size else 0.3
    target = rho * np.outer(sig, sig)
    np.fill_diagonal(target, sig ** 2)
    if shrink_delta is None:
        # 启发式：样本越少/维度越高 → 收缩越强
        t = R.shape[0]
        shrink_delta = float(min(0.7, max(0.1, n_codes / max(t, 1) * 2.0)))
    cov = (shrink_delta * target + (1.0 - shrink_delta) * sample)
    sigmas = {c: float(sig[i]) for i, c in enumerate(codes_present)}
    covdict = {a: {b: float(cov[i, j]) for j, b in enumerate(codes_present)} for i, a in enumerate(codes_present)}
    return sigmas, covdict


def scale_to_target_vol(
    weights: dict[str, float],
    vols: dict[str, float],
    target_vol: float,
    *,
    corr: float = 0.3,
    cov: dict[str, dict[str, float]] | None = None,
) -> dict[str, float]:
    """把组合年化波动率缩放到 target_vol。

    优先用全协方差矩阵 ``cov``（dict-of-dict，由 estimate_covariance 产出），
    体现真实成对相关（同板块高、跨板块低）；缺省回退到单一 ``corr=0.3`` 旧口径。
    """
    if not weights:
        return weights
    codes = list(weights.keys())
    w = np.array([weights[c] for c in codes], dtype=float)
    sig = np.array([vols.get(c, 0.2) for c in codes], dtype=float)
    if cov is not None:
        cov_arr = np.array(
            [[cov.get(a, {}).get(b, (sig[i] * sig[j] * corr if i != j else sig[i] ** 2))
              for j, b in enumerate(codes)]
             for i, a in enumerate(codes)],
            dtype=float,
        )
        port_vol = float(np.sqrt(max(w @ cov_arr @ w, 1e-12)))
    else:
        var = float((w ** 2 * sig ** 2).sum())
        pair = float((w[:, None] * w[None, :] * np.outer(sig, sig)).sum())
        diag = float((w ** 2 * sig ** 2).sum())
        cross = (pair - diag) * corr
        port_vol = float(np.sqrt(max(var + cross, 1e-12)))
    if port_vol < 1e-9:
        return weights
    k = target_vol / port_vol
    return {c: float(weights[c] * k) for c in codes}
