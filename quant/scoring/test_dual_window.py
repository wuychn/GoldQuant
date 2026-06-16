"""双窗口概念权重合成。"""

from __future__ import annotations

from unittest.mock import patch

from quant.scoring.theme_tracker import (
    _blend_dual_window_scores,
    build_scoring_concept_scores,
)


def test_blend_dual_window_scores() -> None:
    pool = {"A", "B"}
    structural = {"A": 80.0, "B": 40.0}
    momentum = {"A": 30.0, "B": 90.0}
    dw = {"structural_weight": 0.6, "momentum_weight": 0.4}
    out = _blend_dual_window_scores(pool, structural, momentum, dw)
    assert out["A"] == 60.0
    assert out["B"] == 60.0


_CFG = {
    "dual_window": {
        "enabled": True,
        "structural_days": 10,
        "momentum_days": 3,
        "structural_weight": 0.6,
        "momentum_weight": 0.4,
    },
    "fund_log_scale": False,
    "recency_decay": 1.0,
    "score_weights": {
        "selection_count": 50,
        "composite_gain": 30,
        "net_fund_flow": 20,
    },
}


def _metrics(_st, _payload, *, lookback: int, section: str = "概念板块"):
    if lookback == 10:
        return {
            "热": {"入选次数": 2.0, "综合涨幅": 10.0, "资金净流入": 20.0},
            "冷": {"入选次数": 2.0, "综合涨幅": 11.0, "资金净流入": 22.0},
        }
    return {
        "热": {"入选次数": 3.0, "综合涨幅": 20.0, "资金净流入": 100.0},
        "冷": {"入选次数": 0.0, "综合涨幅": 0.0, "资金净流入": 0.0},
    }


@patch("quant.scoring.theme_tracker._load_state", return_value={"daily": {}})
@patch("quant.scoring.theme_tracker.build_scoring_concept_pool", return_value={"热", "冷"})
@patch("quant.scoring.theme_tracker.collect_concept_window_metrics", side_effect=_metrics)
@patch("quant.scoring.theme_tracker._concept_tracker_cfg", return_value=_CFG)
def test_dual_window_boosts_momentum_leader(*_mocks) -> None:
    scores = build_scoring_concept_scores({}, {"daily": {}})
    assert scores["热"] > scores["冷"]
