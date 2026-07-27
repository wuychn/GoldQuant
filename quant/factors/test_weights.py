"""Phase 5 IC 驱动权重测试。"""

from __future__ import annotations

from quant.factors.weights import full_weight_map, icir_weights


def _report(**overrides):
    base = {
        "good": {"ic_mean": 0.05, "ic_std": 0.1, "icir": 0.5, "t_stat": 3.0},
        "weak": {"ic_mean": 0.01, "ic_std": 0.1, "icir": 0.1, "t_stat": 0.8},
        "neg": {"ic_mean": -0.03, "ic_std": 0.1, "icir": -0.3, "t_stat": -2.0},
        "strong": {"ic_mean": 0.08, "ic_std": 0.1, "icir": 0.8, "t_stat": 5.0},
    }
    base.update(overrides)
    return base


def test_icir_weights_keeps_only_positive_significant():
    w = icir_weights(_report(), min_tstat=1.0)
    assert "good" in w and "strong" in w
    assert "weak" not in w  # t_stat 0.8 < 1.0
    assert "neg" not in w  # 负 IC
    # strong(gir=0.8) > good(0.5)
    assert w["strong"] > w["good"]


def test_full_weight_map_covers_all_factors_zero_fill():
    full = full_weight_map(_report(), ["good", "weak", "neg", "strong", "missing"], min_tstat=1.0)
    assert set(full) == {"good", "weak", "neg", "strong", "missing"}
    assert full["good"] > 0 and full["strong"] > 0
    assert full["weak"] == 0.0
    assert full["neg"] == 0.0
    assert full["missing"] == 0.0  # IC 报告无此因子 → 0


def test_compose_alpha_skips_zero_weights():
    """零权重因子在 compose_alpha 中被跳过（验证全权重表的作用）。"""
    from quant.factors.base import FactorRow
    from quant.factors.compose import compose_alpha

    rows = [
        FactorRow(date="2024-01-01", code="000001", raw={"good": 1.0, "neg": -2.0}, neutral={"good": 1.0, "neg": -2.0}),
        FactorRow(date="2024-01-01", code="000002", raw={"good": -1.0, "neg": 2.0}, neutral={"good": -1.0, "neg": 2.0}),
    ]
    # 仅 good 有权重，neg 权重 0 → alpha 仅由 good 决定
    alpha = compose_alpha(rows, weights={"good": 1.0, "neg": 0.0})
    assert alpha["000001"] > 0 and alpha["000002"] < 0


def test_empty_report_returns_empty():
    assert icir_weights({}) == {}
    assert full_weight_map({}, ["a", "b"]) == {"a": 0.0, "b": 0.0}
