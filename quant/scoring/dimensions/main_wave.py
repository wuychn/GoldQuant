"""主升浪专属评分维度：加速段 / 趋势内回调 / 买点。"""

from __future__ import annotations

from quant.config import load_gates_config
from quant.scoring.context import ScoreContext
from quant.scoring.dimensions.base import clamp
from quant.scoring.models import DimensionResult
from quant.strategy.main_wave import (
    PHASE_ACCEL,
    PHASE_PULLBACK,
    detect_buy_setup,
    main_wave_phase,
    ma_bull_stack,
    ma_diverging,
    _mas,
)


class MainWaveScorer:
    name = "main_wave"

    def score(self, ctx: ScoreContext, stock: dict) -> DimensionResult:
        mw_cfg = (load_gates_config().get("main_wave") or {})
        m = _mas(stock)
        if m.get("ma5") is None:
            return DimensionResult(self.name, 0, 0, True, available=False, detail={})

        min_spread = float(mw_cfg.get("min_ma_spread_pct", 0.8))
        ok, phase, phase_note = main_wave_phase(stock, mw_cfg)
        bull = ma_bull_stack(m)
        diverge = ma_diverging(m, min_spread_pct=min_spread)
        ok_buy, kind, _ = detect_buy_setup(stock, ctx, mw_cfg)

        s = 15.0
        if ok and phase == PHASE_ACCEL:
            s += 40
        elif ok and phase == PHASE_PULLBACK:
            s += 28
        elif ok:
            s += 18
        if bull:
            s += 10
        if diverge:
            s += 10
        if ok_buy:
            s += 12

        return DimensionResult(
            self.name,
            clamp(s),
            0,
            True,
            detail={
                "主升波段": ok,
                "阶段": phase or None,
                "阶段说明": phase_note if ok else phase_note,
                "均线多头": bull,
                "均线发散": diverge,
                "买点类型": kind or None,
            },
        )
