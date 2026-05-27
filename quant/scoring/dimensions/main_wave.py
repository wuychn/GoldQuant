"""主升浪专属评分维度：主线龙头 + 均线发散 + 趋势质量。"""

from __future__ import annotations

from quant.config import load_gates_config
from quant.scoring.context import ScoreContext
from quant.scoring.dimensions.base import clamp
from quant.scoring.models import DimensionResult
from quant.strategy.main_wave import detect_buy_setup, is_theme_leader, ma_bull_stack, ma_diverging, _mas


class MainWaveScorer:
    name = "main_wave"

    def score(self, ctx: ScoreContext, stock: dict) -> DimensionResult:
        mw_cfg = (load_gates_config().get("main_wave") or {})
        m = _mas(stock)
        if m.get("ma5") is None:
            return DimensionResult(self.name, 0, 0, True, available=False, detail={})

        max_rank = int(mw_cfg.get("leader_max_rank", 15))
        min_spread = float(mw_cfg.get("min_ma_spread_pct", 0.8))
        leader = is_theme_leader(stock, ctx, max_rank=max_rank)
        bull = ma_bull_stack(m)
        diverge = ma_diverging(m, min_spread_pct=min_spread)
        ok_buy, kind, _ = detect_buy_setup(stock, ctx, mw_cfg)

        s = 20.0
        if leader:
            s += 25
        if bull:
            s += 20
        if diverge:
            s += 25
        if ok_buy:
            s += 10

        return DimensionResult(
            self.name,
            clamp(s),
            0,
            True,
            detail={
                "主线龙头": leader,
                "均线多头": bull,
                "均线发散": diverge,
                "买点类型": kind or None,
            },
        )
