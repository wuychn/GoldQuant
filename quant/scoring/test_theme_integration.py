"""行业映射 + 概念/行业分轨评分集成验证。"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

from quant.scoring.context import ScoreContext
from quant.scoring.dimensions.concept_theme import ConceptThemeScorer, _stock_concepts, _stock_industry
from quant.scoring.industry_aliases import expand_industries, load_industry_alias_maps, reload_industry_aliases_cache
from quant.scoring.theme_boards import BOARD_CONCEPT, BOARD_INDUSTRY
from quant.scoring.theme_tracker import (
    build_scoring_concept_pool,
    build_scoring_concept_scores,
    score_theme_resonance,
)


def _sample_payload() -> dict:
    return {
        "概念板块": {
            "涨幅榜": [{"行业": "CPO概念", "行业-涨跌幅": 5.2, "净额": 10.0}],
            "资金流入榜": [{"行业": "CPO概念", "净额": 10.0, "行业-涨跌幅": 5.2}],
        },
        "行业板块": {
            "涨幅榜": [{"板块": "元件", "涨跌幅": 3.1, "净流入": 5.0}],
            "资金流入榜": [{"板块": "元件", "净流入": 5.0, "涨跌幅": 3.1}],
        },
        "同花顺人气榜": [
            {
                "股票代码": "002463",
                "所属概念": "CPO概念",
            }
        ],
    }


@contextmanager
def _mock_dual_track_scores(*, concept_nets: dict[str, float], industry_nets: dict[str, float]):
    concept_pool = set(concept_nets)
    industry_pool = set(industry_nets)

    def pool(_payload, _state, section=BOARD_CONCEPT):
        return concept_pool if section == BOARD_CONCEPT else industry_pool

    def scores(_payload, _state, mode="", section=BOARD_CONCEPT):
        return concept_nets if section == BOARD_CONCEPT else industry_nets

    with (
        patch("quant.scoring.theme_tracker._past_board_trading_days", return_value=[]),
        patch(
            "quant.scoring.theme_tracker._today_gain_fund_boards",
            side_effect=lambda _p, _d, limit=10, section=BOARD_CONCEPT: (
                (concept_pool, concept_pool) if section == BOARD_CONCEPT else (industry_pool, industry_pool)
            ),
        ),
        patch("quant.scoring.theme_tracker.build_scoring_concept_pool", side_effect=pool),
        patch("quant.scoring.theme_tracker.build_scoring_concept_scores", side_effect=scores),
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


def test_industry_alias_direction_em_to_ths() -> None:
    reload_industry_aliases_cache()
    _, em_to_ths = load_industry_alias_maps()
    assert em_to_ths.get("塑料") == "塑料制品"
    expanded = expand_industries({"塑料", "元件"})
    assert expanded == {"塑料", "塑料制品", "元件"}


def test_concept_pool_excludes_industry_board_names() -> None:
    payload = _sample_payload()
    concept_pool = build_scoring_concept_pool(payload, {"daily": {}}, section=BOARD_CONCEPT)
    industry_pool = build_scoring_concept_pool(payload, {"daily": {}}, section=BOARD_INDUSTRY)
    assert "CPO概念" in concept_pool
    assert "元件" not in concept_pool
    assert "元件" in industry_pool
    assert "CPO概念" not in industry_pool


def test_concept_tag_does_not_score_on_industry_track() -> None:
    with _mock_dual_track_scores(concept_nets={}, industry_nets={"元件": 90.0}):
        score, detail = score_theme_resonance({"CPO概念"}, set(), {})
    assert score == 0.0
    assert detail["概念共振"].get("命中概念", []) == []
    assert detail["行业共振"].get("命中行业", []) == []


def test_industry_tag_scores_on_industry_track_only() -> None:
    with _mock_dual_track_scores(concept_nets={}, industry_nets={"元件": 88.0}):
        score, detail = score_theme_resonance(set(), {"元件"}, {})
    assert score == 88.0
    assert detail["最佳赛道"] == "行业"
    assert detail["行业共振"]["命中行业"] == ["元件"]


def test_concept_beats_industry_when_higher_score() -> None:
    with _mock_dual_track_scores(concept_nets={"CPO概念": 95.0}, industry_nets={"元件": 70.0}):
        score, detail = score_theme_resonance({"CPO概念"}, {"元件"}, {})
    assert score == 95.0
    assert detail["最佳赛道"] == "概念"


def test_scorer_end_to_end_with_popularity_concept() -> None:
    payload = _sample_payload()
    stock = {"股票代码": "002463", "行业": "元件"}
    with _mock_dual_track_scores(concept_nets={"CPO概念": 92.0}, industry_nets={"元件": 80.0}):
        result = ConceptThemeScorer().score(ScoreContext(payload=payload), stock)
    assert result.score == 92.0
    assert result.detail["最佳赛道"] == "概念"
    assert result.detail["个股概念"] == ["CPO概念"]
    assert result.detail["个股行业"] == ["元件"]
    assert "PCB概念" not in result.detail["个股概念"]
