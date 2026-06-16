"""theme_detail 回归：不应引用未定义变量。"""

from __future__ import annotations

from quant.scoring.theme_tracker import theme_detail


def test_theme_detail_runs_without_stock_concepts() -> None:
    out = theme_detail(
        {"概念板块": {"涨幅榜": [], "资金流入榜": []}},
        mode="post_market_evening",
    )
    assert "概念权重分" in out
    assert "权重分靠前" in out
