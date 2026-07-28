"""P3 组合层单元测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant.portfolio.buffer import apply_buffer, apply_rank_buffer
from quant.portfolio.constraints import (
    apply_concept_cap,
    apply_sector_cap,
    apply_single_cap,
    truncate_to_n,
)
from quant.portfolio.target import TargetPortfolio
from quant.portfolio.voltarget import inv_vol_weights, realized_vol, scale_to_target_vol


def test_inv_vol_weights_caps():
    vols = {"a": 0.1, "b": 0.4, "c": 0.2}
    w = inv_vol_weights(vols, max_weight=0.5)
    assert abs(sum(w.values()) - 1.0) < 1e-6
    assert all(v <= 0.5 + 1e-6 for v in w.values())
    # 低波动票权重更大
    assert w["a"] > w["b"]


def test_scale_to_target_vol():
    vols = {"a": 0.1, "b": 0.2}
    w = {"a": 0.5, "b": 0.5}
    scaled = scale_to_target_vol(w, vols, target_vol=0.05)
    # 缩放后应降低（原组合波动高于 5%）
    assert sum(scaled.values()) < sum(w.values())


def test_buffer_reduces_churn():
    target = {"a": 0.10, "b": 0.20}
    current = {"a": 0.105, "b": 0.195}
    out = apply_buffer(target, current, abs_tol=0.01, rel_tol=0.20, drop_tol=0.015)
    # 小幅漂移保持当前
    assert abs(out["a"] - current["a"]) < 1e-9
    assert abs(out["b"] - current["b"]) < 1e-9


def test_constraints():
    w = {"a": 0.5, "b": 0.3, "c": 0.2}
    c = apply_single_cap(w, 0.4)
    assert c["a"] <= 0.4 + 1e-6
    secs = {"a": "钢铁", "b": "钢铁", "c": "银行"}
    s = apply_sector_cap(c, secs, 0.5)
    steel = s["a"] + s["b"]
    assert steel <= 0.5 + 1e-6
    t = truncate_to_n({"a": 0.4, "b": 0.3, "c": 0.2, "d": 0.1}, 2)
    assert set(t) == {"a", "b"}


def test_rank_buffer_and_concept_cap():
    alpha = {"a": 1.0, "b": 0.9, "c": 0.8, "d": 0.7, "e": 0.1}
    # 已持仓 d：虽不在 top enter，但 rank<=n_exit 应保留
    kept = apply_rank_buffer(alpha, {"d": 0.2}, n_enter=2, n_exit=4)
    assert "a" in kept and "b" in kept and "d" in kept
    assert "e" not in kept
    w = {"a": 0.4, "b": 0.4, "c": 0.2}
    concepts = {"a": ["AI", "芯片"], "b": ["AI"], "c": ["银行"]}
    capped = apply_concept_cap(w, concepts, 0.5)
    assert capped["a"] + capped["b"] <= 0.5 + 1e-6


def test_target_portfolio_end_to_end():
    rng = np.random.default_rng(3)
    rows = []
    dates = pd.bdate_range(end="2024-12-31", periods=40).strftime("%Y%m%d")
    for code in ["000001", "000002", "000003", "000004", "000005"]:
        p = 10.0
        for d in dates:
            p = max(p * (1 + rng.normal(0, 0.03)), 1.0)
            rows.append({"code": code, "date": d, "open": p, "high": p, "low": p, "close": p, "volume": 1e6})
    daily = pd.DataFrame(rows)
    tp = TargetPortfolio(
        max_stocks=3, n_enter=3, n_exit=5, target_vol=0.15, max_weight=0.4, sector_cap=0.9, daily=daily
    )
    alpha = {"000001": 1.0, "000002": 0.8, "000003": 0.5, "000004": 0.3, "000005": 0.1}
    prices = {c: 10.0 for c in alpha}
    w = tp.target_weights(alpha, prices, {}, dates[-1])
    assert len(w) <= 3
    assert all(v >= 0 for v in w.values())
    assert sum(w.values()) <= 1.0 + 1e-6
