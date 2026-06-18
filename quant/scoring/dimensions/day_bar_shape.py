"""当日 K 线形态：收阴线、冲高回落减分（加自选/评分用）。"""

from __future__ import annotations

from typing import Any

from quant.config import load_scoring_config
from quant.scoring.context import ScoreContext
from quant.scoring.dimensions.base import clamp
from quant.scoring.models import DimensionResult
from quant.scoring.tech_indicators import quote_last_price, quote_open_price, to_float


def _session_high(stock: dict) -> float | None:
    pk = stock.get("盘口") if isinstance(stock.get("盘口"), dict) else {}
    high = to_float(pk.get("最高"))
    if high and high > 0:
        return high
    hist = stock.get("历史行情")
    if isinstance(hist, list) and hist:
        row = hist[-1] if isinstance(hist[-1], dict) else {}
        high = to_float(row.get("最高"))
        if high and high > 0:
            return high
    return None


def score_day_bar_shape(stock: dict, *, cfg: dict[str, Any] | None = None) -> tuple[float, dict[str, Any]]:
    c = cfg or (load_scoring_config().get("dimensions") or {}).get("day_bar_shape") or {}
    base = float(c.get("base_score", 85))
    open_px = quote_open_price(stock)
    last = quote_last_price(stock)
    if open_px is None or last is None or open_px <= 0 or last <= 0:
        return base, {"available": False}

    score = base
    detail: dict[str, Any] = {
        "开盘": round(open_px, 4),
        "最新": round(last, 4),
    }

    if last < open_px:
        body_pct = (open_px - last) / open_px * 100
        max_pen = float(c.get("bearish_penalty_max", 22))
        min_pen = float(c.get("bearish_penalty_min", 0))
        scale = float(c.get("bearish_penalty_scale", 2.5))
        pen = min(max_pen, max(min_pen, body_pct * scale))
        score -= pen
        detail.update(
            {
                "收阴": True,
                "实体跌幅_pct": round(body_pct, 3),
                "阴线扣分": round(pen, 2),
            }
        )
    else:
        detail["收阴"] = False

    high = _session_high(stock)
    if high and high > last:
        fade_pct = (high - last) / high * 100
        min_fade = float(c.get("spike_fade_min_pct", 1.5))
        detail["高点回撤_pct"] = round(fade_pct, 3)
        if fade_pct >= min_fade:
            max_pen = float(c.get("spike_fade_penalty_max", 18))
            scale = float(c.get("spike_fade_penalty_scale", 2.0))
            pen = min(max_pen, (fade_pct - min_fade) * scale)
            score -= pen
            detail["冲高回落"] = True
            detail["回落扣分"] = round(pen, 2)
        else:
            detail["冲高回落"] = False
    else:
        detail["冲高回落"] = False

    return clamp(score, lo=float(c.get("min_score", 15)), hi=100.0), detail


class DayBarShapeScorer:
    name = "day_bar_shape"

    def score(self, ctx: ScoreContext, stock: dict) -> DimensionResult:
        del ctx
        cfg = (load_scoring_config().get("dimensions") or {}).get(self.name) or {}
        s, detail = score_day_bar_shape(stock, cfg=cfg)
        available = detail.get("available") is not False
        if not available:
            detail = {}
        return DimensionResult(self.name, s, 0, True, available=available, detail=detail)
