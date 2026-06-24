"""近端动能判定测试。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from quant.strategy.main_wave import is_main_wave_acceleration
from quant.strategy.momentum import (
    intraday_spike_fade,
    momentum_fading,
    momentum_score,
    spread_accel_dual_window,
)
from quant.strategy.trend import PHASE_UP, PHASE_WEAK, quantify_trend


def _hist(closes: list[float], *, volumes: list[float] | None = None) -> list[dict]:
    rows = []
    prev = closes[0]
    vols = volumes or [1_000_000.0] * len(closes)
    for i, c in enumerate(closes):
        chg = (c - prev) / prev * 100 if prev else 0
        rows.append(
            {
                "日期": f"2026-01-{i+1:02d}",
                "收盘": c,
                "开盘": prev,
                "涨跌幅": chg,
                "成交量": vols[i] if i < len(vols) else vols[-1],
            }
        )
        prev = c
    return rows


def _stock(
    closes: list[float],
    *,
    ma5: float,
    ma10: float,
    ma20: float,
    last: float | None = None,
    volumes: list[float] | None = None,
) -> dict:
    live = last if last is not None else closes[-1]
    return {
        "历史行情": _hist(closes, volumes=volumes),
        "盘口": {"最新": live, "涨幅": 0.5},
        "技术指标": {"MA5": ma5, "MA10": ma10, "MA20": ma20, "latest_close": live},
    }


class MomentumLogicTests(unittest.TestCase):
    def test_spread_dual_window_catches_recent_drop(self) -> None:
        """近端发散从峰值回落时应判未加速。"""
        base = [100.0 + i * 0.5 for i in range(85)]
        tail = base[-1]
        closes = base + [tail * 1.02, tail * 1.01, tail * 1.015, tail * 1.01, tail * 1.012]
        cfg = {
            "min_ma_spread_pct": 0.8,
            "spread_accel_days": 5,
            "spread_accel_min_pct": 0.12,
            "spread_recent_days": 3,
            "spread_recent_max_drop_pct": 0.05,
        }
        ok, detail = spread_accel_dual_window(closes, cfg)
        self.assertFalse(ok)
        self.assertIn("发散近端未回落", detail)

    def test_exempt_allows_strong_recent_rally(self) -> None:
        """发散收窄但近5日大涨仍可进加速段。"""
        base = [20.0 + i * 0.08 for i in range(85)]
        closes = base + [c * 1.03 for c in base[-5:]]
        last = closes[-1]
        stock = _stock(closes, ma5=last * 0.99, ma10=last * 0.94, ma20=last * 0.86, last=last)
        cfg = {
            "min_ma_spread_pct": 0.8,
            "spread_accel_days": 5,
            "spread_accel_min_pct": 0.12,
            "spread_recent_days": 3,
            "spread_recent_max_drop_pct": 0.05,
            "spread_exempt_min_signals": 2,
            "momentum_recent_5d_min_pct": 8.0,
            "momentum_high_break_days": 20,
            "momentum_high_break_ratio": 0.97,
            "trend_peak_spread_days": 15,
            "pullback_spread_ratio": 0.55,
            "trend_ma20_floor": 0.985,
            "accel_price_ma5_ratio": 0.97,
            "choppy_lookback_days": 60,
            "choppy_min_net_pct": 5.0,
        }
        ok, note = is_main_wave_acceleration(stock, cfg)
        self.assertTrue(ok, msg=note)

    def test_momentum_fading_on_plateau(self) -> None:
        """高位横盘 + 发散回落 + 涨速放缓 → 衰减。"""
        base = [50.0 + i * 0.3 for i in range(82)]
        plateau = base[-1]
        closes = base + [plateau * 1.001, plateau * 0.999, plateau * 1.002]
        vols = [1_000_000.0] * 82 + [400_000.0, 380_000.0, 390_000.0]
        last = closes[-1]
        stock = _stock(
            closes,
            ma5=last * 0.99,
            ma10=last * 0.97,
            ma20=last * 0.93,
            last=last,
            volumes=vols,
        )
        cfg = {
            "momentum_fade_min_signals": 2,
            "momentum_fade_spread_drop_pct": 0.3,
            "momentum_fade_body_ratio": 0.6,
            "momentum_fade_volume_ratio": 0.8,
            "spread_recent_days": 3,
        }
        fading, note, _ = momentum_fading(stock, cfg)
        self.assertTrue(fading, msg=note)

    def test_intraday_spike_fade_triggers(self) -> None:
        stock = {
            "盘口": {"最新": 133.0, "最高": 142.72, "均价": 135.1, "涨幅": 0.02},
            "历史行情": _hist([100.0 + i * 0.5 for i in range(90)]),
            "技术指标": {"MA5": 130, "MA10": 120, "MA20": 110, "latest_close": 133.0},
        }
        cfg = {"momentum_intraday_fade_min_pct": 5.0, "momentum_intraday_require_below_avg": True}
        ok, note, detail = intraday_spike_fade(stock, cfg)
        self.assertTrue(ok, msg=note)
        self.assertGreater(detail.get("日内高点回撤_pct", 0), 5.0)

    def test_yangjie_like_daily_up_but_intraday_fades(self) -> None:
        base = [114.0 + i * 0.25 for i in range(85)]
        closes = base + [123.13, 128.11, 132.96, 132.99]
        last = 132.99
        stock = _stock(closes, ma5=126.34, ma10=114.29, ma20=109.58, last=last)
        stock["盘口"] = {
            "最新": last,
            "今开": 130.0,
            "最高": 142.72,
            "最低": 129.0,
            "均价": 135.1,
            "涨幅": 0.02,
        }
        cfg = {
            "min_ma_spread_pct": 0.8,
            "spread_accel_days": 5,
            "spread_accel_min_pct": 0.12,
            "spread_recent_days": 3,
            "spread_recent_max_drop_pct": 0.05,
            "momentum_intraday_fade_min_pct": 5.0,
            "momentum_intraday_require_below_avg": True,
            "momentum_fade_min_signals": 2,
            "trend_peak_spread_days": 15,
            "pullback_spread_ratio": 0.55,
            "trend_ma20_floor": 0.985,
            "accel_price_ma5_ratio": 0.97,
            "choppy_lookback_days": 60,
            "choppy_min_net_pct": 5.0,
        }
        phase, note, _ = quantify_trend(stock, cfg)
        ms = momentum_score(stock, cfg)[0]
        self.assertEqual(phase, PHASE_WEAK)
        self.assertLess(ms, 80.0)

    @unittest.skipUnless(
        (Path.home() / ".quant/daily/2026-06-24/raw/during_1343.json").is_file(),
        "需要本地 during 快照",
    )
    def test_live_samples_rank_shift(self) -> None:
        payload = json.loads(
            (Path.home() / ".quant/daily/2026-06-24/raw/during_1343.json").read_text(encoding="utf-8")
        )
        by = {str(r.get("股票代码", "")).strip(): r for r in payload.get("自选股") or []}
        cfg = {
            "min_ma_spread_pct": 0.8,
            "spread_accel_days": 5,
            "spread_accel_min_pct": 0.12,
            "spread_recent_days": 3,
            "spread_recent_max_drop_pct": 0.05,
            "spread_exempt_min_signals": 2,
            "momentum_recent_5d_min_pct": 8.0,
            "momentum_high_break_days": 20,
            "momentum_high_break_ratio": 0.97,
            "momentum_fade_min_signals": 2,
            "momentum_fade_spread_drop_pct": 0.3,
            "trend_peak_spread_days": 15,
            "pullback_spread_ratio": 0.55,
            "trend_ma20_floor": 0.985,
            "accel_price_ma5_ratio": 0.97,
            "choppy_lookback_days": 60,
            "choppy_min_net_pct": 5.0,
        }
        heng = by.get("600487")
        yuan = by.get("600869")
        self.assertIsNotNone(heng)
        self.assertIsNotNone(yuan)
        phase_h, _, _ = quantify_trend(heng, cfg)
        phase_y, _, _ = quantify_trend(yuan, cfg)
        ms_h = momentum_score(heng, cfg)[0]
        ms_y = momentum_score(yuan, cfg)[0]
        self.assertIn(phase_h, (PHASE_WEAK, PHASE_UP))
        self.assertEqual(phase_y, PHASE_UP)
        self.assertGreater(ms_y, ms_h)


if __name__ == "__main__":
    unittest.main()
