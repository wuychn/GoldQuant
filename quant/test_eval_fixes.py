"""评估文档修复项回归测试。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from quant.backtest.broker import SimBroker, Trade
from quant.backtest.stats import newey_west_sharpe
from quant.data.listing import build_listing_map_from_daily, listing_days_as_of
from quant.portfolio.constraints import alpha_strength_weights, apply_style_cap


def test_newey_west_sharpe_finite():
    rng = np.random.default_rng(0)
    rets = rng.normal(0.0005, 0.01, 300)
    s = newey_west_sharpe(rets)
    assert np.isfinite(s)


def test_alpha_strength_weights_positive():
    alpha = {"a": 2.0, "b": 1.0, "c": 0.0}
    w = alpha_strength_weights(alpha, ["a", "b", "c"], full_invest=0.95, shrink=0.3)
    assert abs(sum(w.values()) - 0.95) < 1e-6
    assert w["a"] > w["b"] > w["c"]


def test_apply_style_cap():
    w = {"a": 0.3, "b": 0.3, "c": 0.4}
    buckets = {"a": "small", "b": "small", "c": "large"}
    out = apply_style_cap(w, buckets, {"small": 0.4})
    assert out["a"] + out["b"] <= 0.4 + 1e-6


def test_listing_days_from_first_bar():
    rows = []
    for d in pd.bdate_range("2025-01-01", periods=130).strftime("%Y-%m-%d"):
        rows.append({"code": "000001", "date": d, "close": 10.0})
    daily = pd.DataFrame(rows)
    mp = build_listing_map_from_daily(daily)
    days = listing_days_as_of("000001", "2025-06-30", listing_map=mp, daily=daily)
    assert days >= 100


def test_load_factor_weights_strict_oos(monkeypatch):
    from quant import config as cfg_mod
    from quant.store import paths as pmod

    root = Path(tempfile.mkdtemp())
    monkeypatch.setattr(pmod, "quant_home", lambda: root)
    cfg_mod.reload_config_cache()

    static = root / "config" / "factor_weights.yml"
    static.parent.mkdir(parents=True, exist_ok=True)
    static.write_text(
        yaml.safe_dump({"apply": True, "fit_end": "2099-12-31", "weights": {"mom_20": 1.0}}),
        encoding="utf-8",
    )
    cfg_mod.reload_config_cache()
    info = cfg_mod.load_factor_weights_info(as_of="2024-01-01")
    assert info is None

    ts = root / "config" / "factor_weights_ts.yml"
    ts.write_text(
        yaml.safe_dump(
            {
                "apply": True,
                "weights_ts": {
                    "2024-01-10": {
                        "weights": {"mom_20": 0.8},
                        "train_start": "2023-01-01",
                        "train_end": "2024-01-09",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    cfg_mod.reload_config_cache()
    info2 = cfg_mod.load_factor_weights_info(as_of="2024-01-15")
    assert info2 is not None
    assert info2["source"] == "walk_forward"
    assert info2["weights"]["mom_20"] == 0.8


def test_optimize_mvo():
    from quant.portfolio.mvo import optimize_mvo

    codes = ["a", "b", "c"]
    alpha = {"a": 1.0, "b": 0.5, "c": 0.2}
    sigmas = {"a": 0.2, "b": 0.25, "c": 0.3}
    cov = {c: {d: sigmas[c] * sigmas[d] * (0.3 if c != d else 1.0) for d in codes} for c in codes}
    w = optimize_mvo(codes, alpha, cov, sigmas, max_weight=0.5, full_invest=0.95)
    assert abs(sum(w.values()) - 0.95) < 1e-5
    assert w["a"] >= w["b"] >= w["c"]


def test_sqrt_law_slippage_increases_with_participation():
    from quant.execution.sim_rules import TradeSimConfig
    from quant.execution.slippage import SlippageContext, effective_slippage_pct

    cfg = TradeSimConfig(slippage_model="sqrt_law", slippage_pct=0.001, slippage_sqrt_k=0.5)
    low = effective_slippage_pct(cfg, SlippageContext(volatility_pct=2.0, amount=1e5, adv_amount=1e8, participation=0.01))
    high = effective_slippage_pct(cfg, SlippageContext(volatility_pct=2.0, amount=1e7, adv_amount=1e8, participation=0.15))
    assert high > low


def test_capacity_metrics():
    from quant.backtest.attribution import capacity_metrics

    b = SimBroker(cash=0)
    b.trades = [
        Trade(date="2024-01-02", code="000001", side="buy", shares=1000, price=10.0, cost=5.0),
    ]
    daily = pd.DataFrame(
        {
            "code": ["000001"] * 25,
            "date": pd.bdate_range("2024-01-01", periods=25).strftime("%Y-%m-%d"),
            "amount": [1e8] * 25,
            "close": [10.0] * 25,
        }
    )
    m = capacity_metrics(b, daily, initial_cash=1e6, participation_rate=0.1)
    assert m["n_buys"] == 1
    assert m["median_participation_pct"] >= 0
