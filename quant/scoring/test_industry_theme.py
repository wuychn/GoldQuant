"""个股行业 + 概念分轨参与 concept_theme 共振。"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

from quant.scoring.concept_theme import _stock_concepts, _stock_industry
from quant.scoring.industry_aliases import expand_industries, reload_industry_aliases_cache
from quant.scoring.theme_tracker import score_theme_resonance, snapshot_boards
from quant.scoring.theme_boards import BOARD_CONCEPT, BOARD_INDUSTRY


def test_stock_concepts_no_alias_expansion() -> None:
    tags = _stock_concepts({"所属概念": "MLCC概念"})
    assert tags == {"MLCC概念"}
    assert "PCB概念" not in tags


def test_snapshot_boards_split_by_section() -> None:
    payload = {
        "概念板块": {
            "涨幅榜": [{"行业": "CPO概念", "行业-涨跌幅": 5.0, "净额": 1.0}],
            "资金流入榜": [],
        },
        "行业板块": {
            "涨幅榜": [{"板块": "元件", "涨跌幅": 8.0, "净流入": 2.0}],
            "资金流入榜": [{"板块": "元件", "涨跌幅": 1.0, "净流入": 9.0}],
        },
    }
    cg, cf = snapshot_boards(payload, limit=10, section=BOARD_CONCEPT)
    ig, inf = snapshot_boards(payload, limit=10, section=BOARD_INDUSTRY)
    assert "CPO概念" in cg
    assert "元件" not in cg
    assert "元件" in ig


@contextmanager
def _mock_industry_hit():
    nets = {"元件": 88.0}
    with (
        patch("quant.scoring.theme_tracker._past_board_trading_days", return_value=[]),
        patch(
            "quant.scoring.theme_tracker._today_gain_fund_boards",
            return_value=({"元件"}, {"元件"}),
        ),
        patch(
            "quant.scoring.theme_tracker.build_scoring_concept_pool",
            side_effect=lambda _p, _s, section=BOARD_CONCEPT: {"元件"} if section == BOARD_INDUSTRY else set(),
        ),
        patch(
            "quant.scoring.theme_tracker.build_scoring_concept_scores",
            side_effect=lambda _p, _s, mode="", section=BOARD_CONCEPT: nets if section == BOARD_INDUSTRY else {},
        ),
        patch("quant.scoring.theme_tracker._load_state", return_value={"daily": {}}),
        patch(
            "quant.scoring.theme_tracker._concept_tracker_cfg",
            return_value={
                "rank_tiers": {"mid_score": 45, "low_score": 5, "off_top_penalty": -50},
                "board_limit": 10,
                "score_weights": {"no_hit": 0},
            },
        ),
    ):
        yield


def test_industry_only_hits_industry_track() -> None:
    reload_industry_aliases_cache()
    with _mock_industry_hit():
        score, detail = score_theme_resonance(set(), expand_industries({"元件"}), {})
    assert score == 88.0
    assert detail["最佳赛道"] == "行业"


def test_concept_does_not_hit_industry_pool() -> None:
    reload_industry_aliases_cache()
    with _mock_industry_hit():
        score, detail = score_theme_resonance({"元件"}, set(), {})
    assert score == 0.0
    assert detail["概念共振"].get("命中概念", []) == []
