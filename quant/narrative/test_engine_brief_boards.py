"""engine_brief 概念/行业榜分开展示。"""

from __future__ import annotations

from quant.narrative.engine_brief import build_engine_brief
from quant.scoring.context import ScoreContext


def test_engine_brief_pre_market_omits_empty_boards() -> None:
    payload = {
        "赚钱效应": {"上涨": 2000, "下跌": 2000, "涨停": 50, "跌停": 10},
        "大盘指数": [{"名称": "上证指数", "涨跌幅": 0.5}],
        "涨停统计": {"最高连板": 5},
    }
    brief = build_engine_brief(ScoreContext(payload=payload), payload, mode="pre_market")
    assert "当日涨幅概念" not in brief
    assert "资金流入行业" not in brief


def test_engine_brief_shows_concept_and_industry_boards_separately() -> None:
    payload = {
        "概念板块": {
            "涨幅榜": [{"行业": "CPO概念", "行业-涨跌幅": 5.0}],
            "资金流入榜": [{"行业": "AI应用", "净额": 1.0}],
        },
        "行业板块": {
            "涨幅榜": [{"板块": "元件", "涨跌幅": 8.0}],
            "资金流入榜": [{"板块": "半导体", "净流入": 9.0}],
        },
        "赚钱效应": {"上涨": 2000, "下跌": 2000, "涨停": 50, "跌停": 10},
        "大盘指数": [{"名称": "上证指数", "涨跌幅": 0.5}],
        "涨停统计": {"最高连板": 5},
    }
    brief = build_engine_brief(ScoreContext(payload=payload), payload, mode="during_market")
    assert "当日涨幅概念：CPO概念" in brief
    assert "资金流入概念：AI应用" in brief
    assert "当日涨幅行业：元件" in brief
    assert "资金流入行业：半导体" in brief
    assert "元件" not in brief.split("当日涨幅概念")[1].split("当日涨幅行业")[0]
