"""个股资金流维度：净流入为正分；净流出为负分，流出占比越大、连续流出越久负分越大。"""

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


def _outflow_ratio_penalty_magnitude(
    ratio_pct: float,
    *,
    heavy_pct: float,
    max_penalty: float,
) -> float:
    """按 |净流出|/流通市值 计算附加惩罚幅度（正数，计分时再取负）。"""
    if ratio_pct <= 0 or max_penalty <= 0:
        return 0.0
    if heavy_pct <= 0:
        return max_penalty
    t = min(1.0, ratio_pct / heavy_pct)
    return t * max_penalty


def _streak_penalty_magnitude(
    streak: int,
    *,
    per_day: float,
    streak_max: float,
) -> float:
    if streak <= 0 or per_day <= 0:
        return 0.0
    return min(streak_max, streak * per_day)


def score_stock_fund_flow(stock: dict, *, cfg: dict[str, Any] | None = None) -> tuple[float, dict[str, Any]]:
    c = cfg or (load_scoring_config().get("dimensions") or {}).get("stock_fund_flow") or {}
    neutral = float(c.get("neutral_score", 0))
    inflow_score = float(c.get("inflow_score", 80))
    inflow_max = float(c.get("inflow_max_score", 95))
    inflow_boost_cap = float(c.get("inflow_boost_cap", 15))
    mild_pct = float(c.get("outflow_mild_ratio_pct", 0.025))
    heavy_pct = float(c.get("outflow_heavy_ratio_pct", 0.12))
    any_outflow_penalty = float(c.get("outflow_any_penalty", 12))
    ratio_penalty_max = float(c.get("outflow_ratio_penalty_max", 75))
    streak_penalty = float(c.get("streak_penalty_per_day", 15))
    streak_max = float(c.get("streak_max_penalty", 60))
    streak_lookback = max(1, int(c.get("streak_lookback_days", 5)))
    score_min = float(c.get("score_min", -100))
    score_max = float(c.get("score_max", 95))

    flow = stock.get("个股资金流") or {}
    if not isinstance(flow, dict) or not flow:
        return neutral, {"available": False}

    net_yuan = _net_yuan_from_intraday(flow)
    if net_yuan is None:
        return neutral, {"available": False}

    float_mv = _float_market_cap_yuan(stock)
    ratio_pct = abs(net_yuan) / float_mv * 100 if float_mv and net_yuan < 0 else 0.0

    ratio_mag = 0.0
    streak_mag = 0.0
    streak = 0

    if net_yuan > 0:
        score = inflow_score
        if float_mv and float_mv > 0 and mild_pct > 0:
            inflow_ratio = net_yuan / float_mv * 100
            boost = min(inflow_boost_cap, inflow_ratio / mild_pct * 5.0)
            score = min(inflow_max, inflow_score + boost)
    elif net_yuan < 0:
        daily_raw = stock.get("个股资金流日线") or []
        daily = [r for r in daily_raw if isinstance(r, dict)][-streak_lookback:]
        streak = _consecutive_outflow_days(daily) if daily else 1
        ratio_mag = _outflow_ratio_penalty_magnitude(
            ratio_pct,
            heavy_pct=heavy_pct,
            max_penalty=ratio_penalty_max,
        )
        streak_mag = _streak_penalty_magnitude(
            streak,
            per_day=streak_penalty,
            streak_max=streak_max,
        )
        score = -(any_outflow_penalty + ratio_mag + streak_mag)
    else:
        score = neutral

    score = clamp(score, lo=score_min, hi=score_max)
    detail = {
        "净额": round(net_yuan / 10_000.0, 2),
        "净额单位": "万元",
        "流通市值": float_mv,
        "流出占流通市值": round(ratio_pct, 4) if ratio_pct else 0.0,
        "流出基础惩罚": round(any_outflow_penalty, 2) if net_yuan < 0 else 0.0,
        "流出占比惩罚": round(ratio_mag, 2) if net_yuan < 0 else 0.0,
        "连续流出天数": streak,
        "连续流出惩罚": round(streak_mag, 2) if net_yuan < 0 else 0.0,
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
