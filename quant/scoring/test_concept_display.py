"""概念粘合度展示名（推送 Top3）。"""

from __future__ import annotations

from quant.scoring.concept_theme import (
    format_stock_concepts_brief,
    stock_concept_display_names,
)


def test_fit_rank_top_three() -> None:
    row = {
        "概念粘合度": [
            {"rank": 1, "concept": "超级电容"},
            {"rank": 2, "concept": "CPO"},
            {"rank": 3, "concept": "AI眼镜"},
            {"rank": 4, "concept": "5G"},
        ],
        "所属概念": ["5G", "超级电容", "CPO", "AI眼镜"],
    }
    assert stock_concept_display_names(row) == ["超级电容", "CPO", "AI眼镜"]
    assert format_stock_concepts_brief(row) == "所属概念超级电容、CPO、AI眼镜"


def test_fallback_to_concept_list() -> None:
    row = {"所属概念": ["新能源", "存储芯片", "光伏概念", "锂电池"]}
    assert stock_concept_display_names(row) == ["新能源", "存储芯片", "光伏概念"]
