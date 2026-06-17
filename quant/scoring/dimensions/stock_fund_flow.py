"""个股资金流维度：净流入加分；净流出按占流通市值比例扣分；连续流出加重。"""

from __future__ import annotations

import re
from typing import Any

from quant.config import load_scoring_config
from quant.scoring.context import ScoreContext
from quant.scoring.dimensions.base import clamp
from quant.scoring.models import DimensionResult


def _parse_amount(v: object) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    m = re.search(r"-?\d+(?:\.\d+)?", s.replace(",", ""))
    if not m:
        return None
    return float(m.group())


def _float_market_cap_yuan(stock: dict) -> float | None:
    for key in ("流通市值", "总市值"):
        v = stock.get(key)
        if isinstance(v, (int, float)) and float(v) > 0:
            return float(v)
    return None


def _net_yuan_from_intraday(flow: dict) -> float | None:
    """当日 flash 净额，单位元（源数据为万元）。"""
    wan = _parse_amount(flow.get("净额"))
    if wan is None:
        return None
    return wan * 10_000.0


def _daily_net_yuan(row: dict) -> float | None:
    for key in ("主力净流入-净额", "净额", "净流入"):
        v = row.get(key)
        if v is None:
            continue
        if isinstance(v, (int, float)):
            return float(v)
        wan = _parse_amount(v)
        if wan is not None:
            # 带「万」字样视为万元
            if "万" in str(v):
                return wan * 10_000.0
            return wan
    return None


def _consecutive_outflow_days(daily: list[dict]) -> int:
    streak = 0
    for row in reversed(daily):
        if not isinstance(row, dict):
            break
        net = _daily_net_yuan(row)
        if net is None:
            break
        if net < 0:
            streak += 1
        else:
            break
    return streak


def _score_outflow_by_ratio(
    ratio_pct: float,
    *,
    neutral: float,
    mild_pct: float,
    heavy_pct: float,
    min_score: float,
) -> float:
    """ratio_pct = |净流出| / 流通市值 × 100。"""
    if ratio_pct <= 0:
        return neutral
    if ratio_pct <= mild_pct:
        t = ratio_pct / mild_pct if mild_pct > 0 else 1.0
        return neutral - t * (neutral - 40.0)
    if ratio_pct >= heavy_pct:
        return min_score
    span = heavy_pct - mild_pct
    t = (ratio_pct - mild_pct) / span if span > 0 else 1.0
    return 40.0 - t * (40.0 - min_score)


def score_stock_fund_flow(stock: dict, *, cfg: dict[str, Any] | None = None) -> tuple[float, dict[str, Any]]:
    c = cfg or (load_scoring_config().get("dimensions") or {}).get("stock_fund_flow") or {}
    neutral = float(c.get("neutral_score", 50))
    inflow_score = float(c.get("inflow_score", 80))
    min_score = float(c.get("min_score", 5))
    mild_pct = float(c.get("outflow_mild_ratio_pct", 0.05))
    heavy_pct = float(c.get("outflow_heavy_ratio_pct", 0.35))
    streak_penalty = float(c.get("streak_penalty_per_day", 8))
    streak_max = float(c.get("streak_max_penalty", 24))
    streak_lookback = max(1, int(c.get("streak_lookback_days", 5)))

    flow = stock.get("个股资金流") or {}
    if not isinstance(flow, dict) or not flow:
        return neutral, {"available": False}

    net_yuan = _net_yuan_from_intraday(flow)
    if net_yuan is None:
        return neutral, {"available": False}

    float_mv = _float_market_cap_yuan(stock)
    ratio_pct = abs(net_yuan) / float_mv * 100 if float_mv and net_yuan < 0 else 0.0

    if net_yuan > 0:
        base = inflow_score
        if float_mv and float_mv > 0:
            inflow_ratio = net_yuan / float_mv * 100
            boost = min(15.0, inflow_ratio / mild_pct * 5.0) if mild_pct > 0 else 0.0
            base = min(95.0, inflow_score + boost)
        score = base
    elif net_yuan < 0:
        score = _score_outflow_by_ratio(
            ratio_pct,
            neutral=neutral,
            mild_pct=mild_pct,
            heavy_pct=heavy_pct,
            min_score=min_score,
        )
    else:
        score = neutral

    daily_raw = stock.get("个股资金流日线") or []
    daily = [r for r in daily_raw if isinstance(r, dict)][-streak_lookback:]
    streak = _consecutive_outflow_days(daily) if daily else (1 if net_yuan < 0 else 0)
    extra = 0.0
    if streak > 1 and net_yuan < 0:
        extra = min(streak_max, (streak - 1) * streak_penalty)
        score -= extra

    score = clamp(score, lo=min_score, hi=95.0)
    detail = {
        "净额": round(net_yuan / 10_000.0, 2),
        "净额单位": "万元",
        "流通市值": float_mv,
        "流出占流通市值": round(ratio_pct, 4) if ratio_pct else 0.0,
        "连续流出天数": streak,
        "连续流出加扣": round(extra, 2),
    }
    return score, detail


class StockFundFlowScorer:
    name = "stock_fund_flow"

    def score(self, ctx: ScoreContext, stock: dict) -> DimensionResult:
        del ctx
        cfg = (load_scoring_config().get("dimensions") or {}).get(self.name) or {}
        s, detail = score_stock_fund_flow(stock, cfg=cfg)
        available = bool(detail.get("available", True))
        if not available:
            detail = {}
        return DimensionResult(self.name, s, 0, True, available=available, detail=detail)
