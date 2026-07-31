"""P1 因子层单元测试：用合成 OHLCV，无网络。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant.factors.base import FactorRow
from quant.factors.compose import compose_alpha, rank_alpha, top_n
from quant.factors.ic import daily_rank_ic, quintile_spread
from quant.factors.library import (
    ALL_FACTORS,
    FLOW_FACTORS,
    FUNDAMENTAL_FACTORS,
    HOT_FACTORS,
    THEME_FACTORS,
    BarSeries,
)
from quant.factors.registry import REGISTRY


def _synth_bars(code: str, n: int = 260, drift: float = 0.001) -> BarSeries:
    rng = np.random.default_rng(42)
    dates = pd.bdate_range(end="2024-12-31", periods=n).strftime("%Y%m%d")
    rets = drift + rng.normal(0, 0.02, n)
    close = 10 * np.exp(np.cumsum(rets))
    high = close * (1 + np.abs(rng.normal(0, 0.005, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, n)))
    open_ = close * (1 + rng.normal(0, 0.002, n))
    volume = (1e6 * (1 + np.abs(rng.normal(0, 0.3, n)))).astype(float)
    amount = volume * close
    turnover = (rng.uniform(0.5, 3.0, n)).astype(float)
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume, "amount": amount, "turnover_rate": turnover},
        index=dates,
    )
    return BarSeries(code=code, df=df)


def test_factor_registry_covers_families():
    names = set(REGISTRY.names())
    assert {"mom_20", "mom_60", "eff_ratio_60", "dist_high_252", "vol_ratio_5_20"}.issubset(names)


def test_factors_compute_on_synthetic():
    bars = _synth_bars("000001")
    as_of = bars.df.index[-1]
    # 依赖 extras/PIT 快照的族（资金/主题/热度/基本面）在无 extras 的合成 bar 上
    # 合法返回 None；纯 OHLCV 因子必须有值。
    allow_none = {
        f.name
        for fam in (FLOW_FACTORS, THEME_FACTORS, HOT_FACTORS, FUNDAMENTAL_FACTORS)
        for f in fam
    }
    for f in ALL_FACTORS:
        v = f.compute(bars, as_of)
        if f.name in allow_none:
            assert v is None or np.isfinite(v), f"{f.name} 返回 {v}"
            continue
        assert v is not None and np.isfinite(v), f"{f.name} 返回 {v}"


def test_compose_alpha_and_rank():
    rows = []
    for i, code in enumerate(["000001", "000002", "000003", "000004", "000005"]):
        bars = _synth_bars(code, drift=0.001 * (i - 2))
        as_of = bars.df.index[-1]
        raw = {}
        for f in ALL_FACTORS:
            v = f.compute(bars, as_of)
            if v is not None and np.isfinite(v):
                raw[f.name] = float(v) * f.direction
        rows.append(FactorRow(date=as_of, code=code, raw=raw))
    alpha = compose_alpha(rows, weights={f.name: f.default_weight for f in ALL_FACTORS}, use_neutral=False)
    assert len(alpha) == 5
    ranked = rank_alpha(alpha)
    assert set(ranked) == set(alpha.keys())
    assert top_n(alpha, 3) == ranked[:3]


def test_ic_report_runs():
    # 造 30 个截面，每个 20 只票，alpha 与前瞻收益正相关
    rng = np.random.default_rng(7)
    rows = []
    for d in range(30):
        date = f"2024-01-{d + 1:02d}"
        for c in range(20):
            z = rng.normal(0, 1)
            fwd = z * 0.5 + rng.normal(0, 1)  # IC > 0
            rows.append(
                FactorRow(
                    date=date,
                    code=f"{c:06d}",
                    raw={"mom_60": z},
                    neutral={"mom_60": z},
                    forward_return_pct=float(fwd),
                )
            )
    ic = daily_rank_ic(rows, "mom_60")
    assert ic["ic_mean"] > 0.1, ic
    assert ic["n_days"] == 30
    qs = quintile_spread(rows, "mom_60")
    assert qs["ls_spread_bps"] > 0, qs
