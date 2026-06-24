"""主升浪专属评分维度：加速段 / 趋势内回调 / 买点。"""

from __future__ import annotations

from quant.config import load_gates_config, load_scoring_config
from quant.scoring.context import ScoreContext
from quant.scoring.dimensions.base import clamp
from quant.scoring.models import DimensionResult
from quant.strategy.main_wave import (
    PHASE_ACCEL,
    PHASE_PULLBACK,
    detect_buy_setup,
    main_wave_phase,
    main_wave_score_penalties,
    ma_bull_stack,
    ma_diverging,
    _mas,
)
from quant.strategy.momentum import momentum_score


class MainWaveScorer:
    name = "main_wave"

    def score(self, ctx: ScoreContext, stock: dict) -> DimensionResult:
        mw_cfg = (load_gates_config().get("main_wave") or {})
        score_cfg = (load_scoring_config().get("dimensions") or {}).get(self.name) or {}
        cfg = {**mw_cfg, **score_cfg}
        m = _mas(stock)
        if m.get("ma5") is None:
            return DimensionResult(self.name, 0, 0, True, available=False, detail={})

        min_spread = float(cfg.get("min_ma_spread_pct", 0.8))
        ok, phase, phase_note = main_wave_phase(stock, mw_cfg)
        bull = ma_bull_stack(m)
        diverge = ma_diverging(m, min_spread_pct=min_spread)
        ok_buy, kind, _ = detect_buy_setup(stock, ctx, mw_cfg)
        stack_buy = bool(cfg.get("accel_stack_buy_bonus", False))

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
        if ok_buy and not (phase == PHASE_ACCEL and not stack_buy):
            s += 12

        penalties, pen_detail = main_wave_score_penalties(stock, cfg, phase=phase or "")
        s -= penalties

        ms, ms_detail = momentum_score(stock, mw_cfg)
        blend = float(cfg.get("momentum_score_blend", 0.25))
        s = s * (1.0 - blend) + ms * blend

        detail = {
            "主升波段": ok,
            "阶段": phase or None,
            "阶段说明": phase_note if ok else phase_note,
            "均线多头": bull,
            "均线发散": diverge,
            "买点类型": kind or None,
            "动能分": round(ms, 1),
        }
        if ms_detail:
            detail["动能明细"] = {k: v for k, v in ms_detail.items() if k != "动能分"}
        if pen_detail:
            detail["软扣分"] = pen_detail
            detail["扣分合计"] = round(penalties, 1)

        return DimensionResult(self.name, clamp(s), 0, True, detail=detail)
