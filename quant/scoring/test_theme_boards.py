"""概念/行业板块分轨读取。"""

from __future__ import annotations

from quant.scoring.theme_boards import (
    BOARD_CONCEPT,
    BOARD_INDUSTRY,
    board_gain_fund_lists,
    section_board_rows,
)


def test_section_board_rows_concept_only() -> None:
    payload = {
        "概念板块": {"涨幅榜": [{"行业": "CPO概念", "行业-涨跌幅": 5.0}]},
        "行业板块": {"涨幅榜": [{"板块": "元件", "涨跌幅": 8.0}]},
    }
    concept_rows = section_board_rows(payload, BOARD_CONCEPT, "涨幅榜", limit=10)
    industry_rows = section_board_rows(payload, BOARD_INDUSTRY, "涨幅榜", limit=10)
    assert concept_rows[0]["行业"] == "CPO概念"
    assert industry_rows[0]["行业"] == "元件"


def test_board_gain_fund_lists_split_sections() -> None:
    payload = {
        "概念板块": {
            "涨幅榜": [{"行业": "CPO概念", "行业-涨跌幅": 5.0}],
            "资金流入榜": [{"行业": "CPO概念", "净额": 1.0}],
        },
        "行业板块": {
            "涨幅榜": [{"板块": "元件", "涨跌幅": 8.0}],
            "资金流入榜": [{"板块": "元件", "净流入": 9.0}],
        },
    }
    cg, cf = board_gain_fund_lists(payload, BOARD_CONCEPT)
    ig, inf = board_gain_fund_lists(payload, BOARD_INDUSTRY)
    assert cg == ["CPO概念"]
    assert cf == ["CPO概念"]
    assert ig == ["元件"]
    assert inf == ["元件"]
