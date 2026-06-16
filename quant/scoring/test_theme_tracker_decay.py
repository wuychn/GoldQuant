"""概念窗口：时间衰减与资金 log 缩放。"""

from __future__ import annotations

from quant.scoring.theme_tracker import (
    _day_recency_weight,
    _prepare_fund_values,
    _scores_from_metrics,
)


def test_recency_weight_recent_day_heavier() -> None:
    assert _day_recency_weight(9, 10, 0.88) > _day_recency_weight(0, 10, 0.88)


def test_fund_log_scale_reduces_outlier_gap() -> None:
    raw = {"A": 266.0, "B": 30.0}
    logged = _prepare_fund_values(raw, cfg={"fund_log_scale": True})
    assert (raw["A"] - raw["B"]) > (logged["A"] - logged["B"])


def test_scores_with_log_fund_less_extreme_than_linear() -> None:
    metrics = {
        "PCB": {"入选次数": 2.0, "综合涨幅": 12.0, "资金净流入": 266.0},
        "CPO": {"入选次数": 2.0, "综合涨幅": 8.0, "资金净流入": 82.0},
        "X": {"入选次数": 1.0, "综合涨幅": 3.0, "资金净流入": 10.0},
    }
    linear_no_log = _scores_from_metrics(
        metrics, cfg={"fund_log_scale": False, "normalize_mode": "minmax"}
    )
    logged_pct = _scores_from_metrics(
        metrics, cfg={"fund_log_scale": True, "normalize_mode": "percentile"}
    )
    # log + 百分位后 PCB 极端资金对 CPO 的挤压应减弱
    gap_linear = linear_no_log["PCB"] - linear_no_log["CPO"]
    gap_logged = logged_pct["PCB"] - logged_pct["CPO"]
    assert gap_logged < gap_linear


def test_percentile_normalization_spreads_ranks() -> None:
    from quant.scoring.theme_tracker import _normalize_metric

    vals = {"A": 1.0, "B": 2.0, "C": 100.0}
    out = _normalize_metric(vals, cfg={"normalize_mode": "percentile"})
    assert out["A"] == 0.0
    assert out["C"] == 100.0
    assert out["B"] == 50.0
