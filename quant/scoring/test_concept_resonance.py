"""concept_theme 窗口排名分档给分。"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

from quant.scoring.theme_boards import BOARD_CONCEPT
from quant.scoring.theme_tracker import score_concept_resonance

_TIER_CFG = {
    "rank_tiers": {"mid_score": 45, "low_score": 5, "off_top_penalty": -50},
    "board_limit": 10,
    "score_weights": {"no_hit": 0},
}


def _nets() -> dict[str, float]:
    return {f"概念{i}": float(100 - i) for i in range(1, 16)}


@contextmanager
def _mock_resonance():
    nets = _nets()
    with (
        patch("quant.scoring.theme_tracker._past_board_trading_days", return_value=[]),
        patch("quant.scoring.theme_tracker._today_gain_fund_boards", return_value=(set(), set())),
        patch(
            "quant.scoring.theme_tracker.build_scoring_concept_pool",
            side_effect=lambda _p, _s, section=BOARD_CONCEPT: set(nets) if section == BOARD_CONCEPT else set(),
        ),
        patch(
            "quant.scoring.theme_tracker.build_scoring_concept_scores",
            side_effect=lambda _p, _s, mode="", section=BOARD_CONCEPT: nets if section == BOARD_CONCEPT else {},
        ),
        patch("quant.scoring.theme_tracker._load_state", return_value={"daily": {}}),
        patch("quant.scoring.theme_tracker._concept_tracker_cfg", return_value=_TIER_CFG),
    ):
        yield


def test_top3_uses_raw_window_score() -> None:
    with _mock_resonance():
        score, detail = score_concept_resonance({"概念3"}, {})
    assert score == 97.0
    assert detail["排名档位"] == "前三"
    assert detail["最高命中排名"] == 3


def test_rank4_to_7_uses_raw_window_score() -> None:
    with _mock_resonance():
        score, detail = score_concept_resonance({"概念5"}, {})
    assert score == 95.0
    assert detail["排名档位"] == "中游(4-7)"
    assert detail["最高命中排名"] == 5


def test_rank8_to_10_uses_raw_window_score() -> None:
    with _mock_resonance():
        score, detail = score_concept_resonance({"概念9"}, {})
    assert score == 91.0
    assert detail["排名档位"] == "边缘(8-10)"


def test_rank11_plus_gets_penalty() -> None:
    with _mock_resonance():
        score, detail = score_concept_resonance({"概念12"}, {})
    assert score == -50.0
    assert detail["排名档位"] == "榜外(11+)"
    assert detail["概念减分"] is True


def test_best_rank_wins_among_multiple_hits() -> None:
    with _mock_resonance():
        score, detail = score_concept_resonance({"概念12", "概念6"}, {})
    assert score == 94.0
    assert detail["最佳命中概念"] == "概念6"
    assert detail["最高命中排名"] == 6


def test_fit_rank_prefers_high_adhesion_over_better_board_rank() -> None:
    fit_order = [("概念12", 1), ("概念6", 2)]
    with _mock_resonance():
        score, detail = score_concept_resonance({"概念12", "概念6"}, {}, concept_fit_order=fit_order)
    assert detail["评分模式"] == "粘合度加权"
    assert detail["最佳命中概念"] == "概念12"
    assert detail["最高命中排名"] == 12
    expected = (-50.0 * 1.0 + 94.0 * 0.85) / (1.0 + 0.85)
    assert abs(score - expected) < 0.01
    assert score < 94.0


def test_fit_rank_weighted_average() -> None:
    fit_order = [("概念3", 1), ("概念12", 2)]
    with _mock_resonance():
        score, detail = score_concept_resonance({"概念3", "概念12"}, {}, concept_fit_order=fit_order)
    assert detail["评分模式"] == "粘合度加权"
    assert detail["最佳命中概念"] == "概念3"
    expected = (97.0 * 1.0 + (-50.0) * 0.85) / (1.0 + 0.85)
    assert abs(score - expected) < 0.01
