"""swap_gate 单测。"""

from __future__ import annotations

import pandas as pd

from quant.portfolio.swap_gate import (
    SwapGateConfig,
    passes_swap_thresholds,
    select_target_codes,
)


def _flat_daily(codes_closes: dict[str, list[float]], start="2024-01-02") -> pd.DataFrame:
    from datetime import date, timedelta

    d0 = date.fromisoformat(start)
    rows = []
    for code, closes in codes_closes.items():
        for i, c in enumerate(closes):
            rows.append(
                {
                    "date": (d0 + timedelta(days=i)).isoformat(),
                    "code": code,
                    "open": c,
                    "high": c * 1.01,
                    "low": c * 0.99,
                    "close": c,
                    "volume": 1e6,
                    "amount": 1e8,
                }
            )
    return pd.DataFrame(rows)


def test_passes_requires_both_score_and_cost():
    cfg = SwapGateConfig(delta_sigma=0.3, cost_cover_k=2.0, alpha_to_ret=0.01)
    sigma = 1.0
    # gap 0.5σ → edge 0.5%；成本 0.2% → 2*0.2%=0.4% → 通过
    assert passes_swap_thresholds(1.0, 0.5, sigma=sigma, cost_frac=0.002, cfg=cfg)
    # 分数差不够
    assert not passes_swap_thresholds(0.2, 0.0, sigma=sigma, cost_frac=0.0001, cfg=cfg)
    # 成本盖不住
    assert not passes_swap_thresholds(1.0, 0.5, sigma=sigma, cost_frac=0.01, cfg=cfg)


def test_keep_not_kicked_by_rank_one():
    # 上行趋势持仓 keep；外部更强新票不得踢掉
    up = [10 + i * 0.1 for i in range(25)]
    daily = _flat_daily({"AAA": up, "BBB": up, "CCC": [9.0] * 25})
    as_of = str(daily["date"].max())
    alpha = {"AAA": 1.0, "BBB": 0.9, "CCC": 5.0, "DDD": 4.0, "EEE": 3.0}
    # 制造截面方差
    for i, c in enumerate(["F1", "F2", "F3", "F4", "F5"]):
        alpha[c] = 0.1 * i
    current = {"AAA": 0.3, "BBB": 0.3}
    cfg = SwapGateConfig(max_stocks=2, n_enter=2, n_exit=8, delta_sigma=0.01, cost_cover_k=0.0)
    dec = select_target_codes(
        alpha,
        current,
        daily=daily,
        as_of=as_of,
        prices={"AAA": 12.0, "BBB": 12.0, "CCC": 9.0},
        cfg=cfg,
        cost_fn=lambda sell, buy: 0.0,
    )
    assert "AAA" in dec.codes
    assert "BBB" in dec.codes
    assert "CCC" not in dec.codes


def test_replaceable_swapped_when_thresholds_pass():
    up = [10 + i * 0.1 for i in range(25)]
    # 末尾大跌 → replaceable / 可能 force_exit；用较短失败避免 force
    weak = up[:-3] + [up[-4] * 0.95, up[-4] * 0.94, up[-4] * 0.93]
    daily = _flat_daily({"OLD": weak, "NEW": up})
    as_of = str(daily["date"].max())
    alpha = {"OLD": 0.0, "NEW": 2.0, "Z1": 0.1, "Z2": -0.1, "Z3": 0.05}
    current = {"OLD": 0.9}
    cfg = SwapGateConfig(
        max_stocks=1,
        n_enter=1,
        n_exit=8,
        delta_sigma=0.1,
        cost_cover_k=1.0,
        alpha_to_ret=0.05,
        trend_fail_days=99,  # 本测不测强制卖
    )
    dec = select_target_codes(
        alpha,
        current,
        daily=daily,
        as_of=as_of,
        prices={"OLD": 10.0, "NEW": 12.0},
        cfg=cfg,
        cost_fn=lambda sell, buy: 0.001,
    )
    assert "NEW" in dec.codes
    assert "OLD" not in dec.codes
    assert dec.swaps == [("OLD", "NEW")]


def test_empty_slot_takes_n_enter_only():
    up = [10 + i * 0.05 for i in range(25)]
    daily = _flat_daily({"A": up, "B": up, "C": up, "D": up})
    as_of = str(daily["date"].max())
    alpha = {"A": 3.0, "B": 2.0, "C": 1.0, "D": 0.5}
    cfg = SwapGateConfig(max_stocks=3, n_enter=2, n_exit=8)
    dec = select_target_codes(
        alpha,
        {},
        daily=daily,
        as_of=as_of,
        prices={k: 10.0 for k in alpha},
        cfg=cfg,
        cost_fn=lambda sell, buy: 0.0,
    )
    assert set(dec.codes) == {"A", "B"}
