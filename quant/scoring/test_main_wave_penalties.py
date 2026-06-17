"""main_wave 软扣分与 enrich 历史 K 线窗口。"""

from __future__ import annotations

import unittest

from quant.scoring.dimensions.main_wave import MainWaveScorer
from quant.scoring.context import ScoreContext
from quant.strategy.main_wave import is_trend_choppy, main_wave_score_penalties


def _daily_closes(closes: list[float]) -> list[dict]:
    rows: list[dict] = []
    prev = closes[0]
    for i, c in enumerate(closes):
        chg = (c - prev) / prev * 100 if prev else 0
        rows.append({"日期": f"2026-01-{i+1:02d}", "收盘": c, "开盘": prev, "最高": c, "最低": prev, "涨跌幅": chg})
        prev = c
    return rows


def _v_spike_stock() -> dict:
    """深跌后垂直拉升：类似宗申动力结构。"""
    closes = [15.0 - i * 0.05 for i in range(85)] + [16.0, 18.0, 20.5, 22.5, 24.0]
    last = closes[-1]
    return {
        "股票代码": "001696",
        "盘口": {"最新": last, "开盘": last * 0.98, "最高": last * 1.02, "均价": last * 0.99},
        "历史行情": _daily_closes(closes),
        "技术指标": {
            "MA5": last * 0.92,
            "MA10": last * 0.82,
            "MA20": last * 0.72,
            "latest_close": last,
        },
    }


class MainWavePenaltyTests(unittest.TestCase):
    def test_v_spike_gets_penalties_not_87(self) -> None:
        stock = _v_spike_stock()
        cfg = {
            "min_ma_spread_pct": 0.8,
            "spread_accel_days": 5,
            "choppy_lookback_days": 60,
            "penalty_v_reversal_max": 22,
            "v_reversal_spread_prev_max": 0.5,
            "v_reversal_min_jump_pct": 12,
            "penalty_parabolic_accel_max": 12,
            "accel_stack_buy_bonus": False,
        }
        pen, detail = main_wave_score_penalties(stock, cfg, phase="加速")
        self.assertGreater(pen, 20.0, detail)
        self.assertIn("V型反转扣分", detail)

        scorer = MainWaveScorer()
        ctx = ScoreContext(payload={}, mode="post_market_evening")
        dr = scorer.score(ctx, stock)
        self.assertLess(dr.score, 70.0, dr.detail)

    def test_choppy_runs_with_30_bars(self) -> None:
        closes = [10.0 + (0.5 if i % 3 == 0 else -0.4) for i in range(30)]
        stock = {
            "历史行情": _daily_closes(closes),
            "盘口": {"最新": closes[-1]},
            "技术指标": {"MA5": 10.0, "MA10": 10.2, "MA20": 10.4, "latest_close": closes[-1]},
        }
        cfg = {"choppy_lookback_days": 60, "choppy_min_net_pct": 5.0, "choppy_path_ratio": 2.0}
        self.assertTrue(is_trend_choppy(stock, cfg))


if __name__ == "__main__":
    unittest.main()
