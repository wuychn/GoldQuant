"""Phase 6 真实协方差 + 收缩回归。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant.portfolio2.voltarget import estimate_covariance, scale_to_target_vol


def _daily_two_codes(corr: float, n: int = 80) -> pd.DataFrame:
    """造两只相关性≈corr 的后复权日线。"""
    rng = np.random.default_rng(7)
    z1 = rng.normal(0, 1, n)
    z2 = corr * z1 + np.sqrt(max(0.0, 1 - corr * corr)) * rng.normal(0, 1, n)
    dates = pd.bdate_range(end="2024-12-31", periods=n).strftime("%Y-%m-%d")
    rows = []
    for code, z in (("000001", z1), ("000002", z2)):
        p = 10.0
        for d, sh in zip(dates, z):
            p = max(p * (1 + sh * 0.02), 1.0)
            rows.append({"code": code, "date": d, "close": p})
    return pd.DataFrame(rows), dates


def test_estimate_covariance_returns_sigmas_and_matrix():
    daily, dates = _daily_two_codes(corr=0.8)
    est = estimate_covariance(daily, ["000001", "000002"], dates[-1], lookback=60)
    assert est is not None
    sigmas, cov = est
    assert set(sigmas) == {"000001", "000002"}
    assert all(v > 0 for v in sigmas.values())
    # 协方差矩阵对称、正对角
    assert cov["000001"]["000002"] == cov["000002"]["000001"]
    assert cov["000001"]["000001"] > 0


def test_high_corr_higher_portfolio_vol_than_scalar_03():
    """两只高相关票：真实协方差估的组合波动 > 单一 ρ=0.3 估值。"""
    daily, dates = _daily_two_codes(corr=0.8)
    est = estimate_covariance(daily, ["000001", "000002"], dates[-1], lookback=60)
    assert est is not None
    sigmas, cov = est
    w = {"000001": 0.5, "000002": 0.5}
    # 真实 cov 下，等权组合年化波动
    arr = np.array([[cov["000001"]["000001"], cov["000001"]["000002"]],
                    [cov["000002"]["000001"], cov["000002"]["000002"]]])
    wv = np.array([0.5, 0.5])
    vol_real = float(np.sqrt(wv @ arr @ wv))
    # 单一 ρ=0.3 下
    s = np.array([sigmas["000001"], sigmas["000002"]])
    var03 = float((wv ** 2 * s ** 2).sum() + 2 * 0.3 * wv[0] * wv[1] * s[0] * s[1])
    vol03 = float(np.sqrt(var03))
    assert vol_real > vol03, (vol_real, vol03)


def test_scale_uses_cov_when_provided():
    """传 cov 时按真实矩阵缩放；不传时回退 ρ=0.3。"""
    daily, dates = _daily_two_codes(corr=0.8)
    est = estimate_covariance(daily, ["000001", "000002"], dates[-1], lookback=60)
    assert est is not None
    sigmas, cov = est
    w = {"000001": 0.5, "000002": 0.5}
    scaled_cov = scale_to_target_vol(w, sigmas, 0.15, cov=cov)
    scaled_rho = scale_to_target_vol(w, sigmas, 0.15, corr=0.3)
    s_cov = sum(scaled_cov.values())
    s_rho = sum(scaled_rho.values())
    # 真实(高相关)组合波动更大 → 缩放后总仓应更小（更保守）
    assert s_cov < s_rho


def test_estimate_returns_none_when_insufficient():
    df = pd.DataFrame({"code": ["000001"], "date": ["2024-01-01"], "close": [10.0]})
    assert estimate_covariance(df, ["000001"], "2024-01-01") is None
