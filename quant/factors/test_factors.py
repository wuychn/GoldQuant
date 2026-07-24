"""因子中性化层单测。"""

from __future__ import annotations

import math

from quant.factors.base import FactorRow
from quant.factors.compose import alpha_scores_0_100, compose_neutral_alpha, map_alpha_to_score
from quant.factors.neutralize import neutralize_cross_section, neutralize_panel_rows
from quant.factors.panel import build_factor_panel
from quant.factors.raw import compute_raw_factors, factor_ma_spread, factor_ret_20d
from quant.factors.report import compare_raw_vs_neutral_ic
from quant.scoring.context import ScoreContext
from quant.scoring.engine import ScoringEngine


def _hist(n: int = 40, *, start: float = 10.0, drift: float = 0.01) -> list[dict]:
    rows = []
    px = start
    for i in range(n):
        px = px * (1 + drift + (0.002 if i % 3 == 0 else -0.001))
        rows.append(
            {
                "日期": f"2026-01-{(i % 28) + 1:02d}",
                "收盘": round(px, 4),
                "成交额": 1e8,
            }
        )
    return rows


def test_raw_factors_from_hist():
    stock = {
        "股票代码": "600000",
        "流通市值": 5e10,
        "历史行情": _hist(40, drift=0.015),
        "个股资金流": {"主力净流入-净额": 1e8},
    }
    raw = compute_raw_factors(stock)
    assert "ma_spread" in raw or "ret_20d" in raw
    assert factor_ret_20d(stock) is not None
    assert factor_ma_spread(stock) is not None


def test_neutralize_removes_industry_mean():
    # 同行业统一偏高 raw → 中性化后应接近 0
    raws = [10.0] * 5 + [0.0] * 5
    inds = ["芯片"] * 5 + ["白酒"] * 5
    caps = [math.log(1e10)] * 10
    neut = neutralize_cross_section(raws, industries=inds, log_mcaps=caps, min_names=5)
    assert all(v is not None for v in neut)
    # 芯片组残差应接近，白酒组接近，跨组差应小于原始差
    chip = [neut[i] for i in range(5)]
    liquor = [neut[i] for i in range(5, 10)]
    assert abs(sum(chip) / 5) < 0.5
    assert abs(sum(liquor) / 5) < 0.5


def test_build_panel_and_compose():
    stocks = []
    for i in range(12):
        stocks.append(
            {
                "股票代码": f"60000{i}",
                "股票名称": f"T{i}",
                "行业": "半导体" if i < 6 else "白酒",
                "流通市值": 1e10 * (1 + i * 0.2),
                "历史行情": _hist(35, start=8 + i * 0.5, drift=0.01 + i * 0.001),
                "个股资金流": {"主力净流入-净额": 1e7 * (i - 5)},
            }
        )
    panel = build_factor_panel(stocks, date="2026-01-10", neutralize=True, min_names=5)
    assert len(panel.rows) >= 8
    assert any(r.neutral for r in panel.rows)
    alphas = compose_neutral_alpha(panel.rows)
    assert len(alphas) >= 5
    scores = alpha_scores_0_100(panel.rows)
    assert all(0 <= s <= 100 for s in scores.values())
    assert 0 <= map_alpha_to_score(0.0) <= 100


def test_ic_compare_smoke():
    rows = []
    for d in range(1, 8):
        for i in range(10):
            raw_v = float(i)
            fwd = float(i) * 0.5 + (1 if d % 2 == 0 else -0.2)
            rows.append(
                FactorRow(
                    date=f"2026-01-{d:02d}",
                    code=f"6000{i}",
                    industry="A" if i < 5 else "B",
                    log_mcap=math.log(1e10 + i),
                    raw={"ma_spread": raw_v, "ret_20d": raw_v / 2},
                    forward_return_pct=fwd,
                )
            )
    neutralize_panel_rows(rows, factor_names=["ma_spread", "ret_20d"], min_names=5)
    from quant.factors.panel import FactorPanel

    report = compare_raw_vs_neutral_ic(FactorPanel(rows=rows), horizon_label="5d", min_names=5)
    assert "composite_alpha" in report
    assert report["composite_alpha"]["raw"]["n_days"] >= 1


def test_score_many_fills_neutral_alpha(monkeypatch):
    monkeypatch.setattr(
        "quant.scoring.engine.load_scoring_config",
        lambda: {
            "watchlist_threshold": 70,
            "dimensions": {
                "neutral_alpha": {"enabled": True, "weight": 10, "min_names": 5},
                "technical": {"enabled": False, "weight": 0},
            },
        },
    )
    engine = ScoringEngine(
        {
            "watchlist_threshold": 70,
            "dimensions": {
                "neutral_alpha": {"enabled": True, "weight": 10, "min_names": 5},
            },
        }
    )
    stocks = []
    for i in range(10):
        stocks.append(
            {
                "股票代码": f"60010{i}",
                "股票名称": f"S{i}",
                "行业": "电子" if i < 5 else "医药",
                "流通市值": 2e10,
                "历史行情": _hist(30, start=10 + i, drift=0.012),
            }
        )
    ctx = ScoreContext.from_payload({})
    scores = engine.score_many(ctx, stocks)
    assert ctx.neutral_alpha_scores
    assert len(scores) == 10
    # 至少一个有 available neutral_alpha
    dims = [d for s in scores for d in s.dimensions if d.name == "neutral_alpha" and d.available]
    assert len(dims) >= 5
