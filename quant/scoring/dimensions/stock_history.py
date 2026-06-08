"""个股历史行情维度：近 30 日大涨占比、均线发散、周/月线向上。"""

from __future__ import annotations

from quant.config import load_scoring_config
from quant.scoring.context import ScoreContext
from quant.scoring.dimensions.base import clamp
from quant.scoring.models import DimensionResult
from quant.scoring.tech_indicators import (
    _period_closes,
    hist_close,
    hist_daily_changes,
    hist_rows_sorted,
    ma_bull_from_closes,
    ma_spread_pct,
    period_trend_up,
)


def _score_big_move_ratio(ratio: float) -> float:
    """5%+ 涨幅日占比越高，得分越高。"""
    return clamp(15 + ratio * 400)


def _score_ma_divergence(closes: list[float], *, min_spread: float) -> tuple[float, dict]:
    if len(closes) < 20:
        return 50.0, {}
    spread = ma_spread_pct(closes)
    if spread is None:
        return 50.0, {}
    bull = ma_bull_from_closes(closes)
    mid = len(closes) // 2
    spread_mid = ma_spread_pct(closes[: mid + 1]) if mid >= 20 else None
    expanding = spread_mid is not None and spread > spread_mid

    s = 25.0
    if bull:
        s += 20
    if spread >= min_spread:
        s += 20
    if expanding:
        s += 20
    if spread >= min_spread * 1.5:
        s += 15
    return clamp(s), {
        "均线多头": bull,
        "均线发散": spread >= min_spread and (expanding or spread >= min_spread * 1.2),
        "MA5-MA20发散": round(spread, 2),
    }


def _score_weekly_monthly(hist: object) -> tuple[float, dict]:
    weekly = _period_closes(hist, period="weekly")
    monthly = _period_closes(hist, period="monthly")
    w_up = period_trend_up(weekly)
    m_up = period_trend_up(monthly)

    s = 35.0
    if w_up:
        s += 32
    if m_up:
        s += 33
    return clamp(s), {"周线向上": w_up, "月线向上": m_up}


class StockHistoryScorer:
    name = "stock_history"

    def score(self, ctx: ScoreContext, stock: dict) -> DimensionResult:
        cfg = (load_scoring_config().get("dimensions") or {}).get(self.name) or {}
        lookback = int(cfg.get("lookback_days", 30))
        big_move_pct = float(cfg.get("big_move_pct", 5.0))
        w_big = float(cfg.get("big_move_weight", 0.40))
        w_ma = float(cfg.get("ma_diverge_weight", 0.35))
        w_wm = float(cfg.get("weekly_monthly_weight", 0.25))
        min_spread = float(cfg.get("min_ma_spread_pct", 0.8))

        hist = stock.get("历史行情") or []
        rows = hist_rows_sorted(hist)
        if len(rows) < 10:
            return DimensionResult(self.name, 50, 0, True, available=False, detail={})

        window = rows[-lookback:]
        changes = hist_daily_changes(window)
        closes: list[float] = []
        for row in window:
            c = hist_close(row)
            if c is not None:
                closes.append(c)

        if len(changes) < 5 or len(closes) < 10:
            return DimensionResult(self.name, 50, 0, True, available=False, detail={})

        big_count = sum(1 for c in changes if c >= big_move_pct)
        big_ratio = big_count / len(changes)
        big_score = _score_big_move_ratio(big_ratio)
        ma_score, ma_detail = _score_ma_divergence(closes, min_spread=min_spread)
        wm_score, wm_detail = _score_weekly_monthly(window)

        weight_sum = w_big + w_ma + w_wm
        if weight_sum <= 0:
            weight_sum = 1.0
        total = (big_score * w_big + ma_score * w_ma + wm_score * w_wm) / weight_sum

        detail = {
            f"近{len(window)}日5%+占比": round(big_ratio, 3),
            f"近{len(window)}日5%+天数": big_count,
            **ma_detail,
            **wm_detail,
        }
        return DimensionResult(self.name, clamp(total), 0, True, detail=detail)
