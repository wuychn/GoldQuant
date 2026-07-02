"""个股资金流维度：净流入为正分；净流出为负分，流出占比越大、连续流出越久负分越大。

数据源口径见 ``quant.market.fund_flow``（评分 / 买入门禁 / 展示 / 快照统一）：
- 智能盯盘：大单流入 − 大单流出；
- 晚间复盘：个股资金流日线当日 ``主力净流入-净额`` 优先，否则回退大单净。
"""

from __future__ import annotations

from typing import Any

from quant.config import load_scoring_config
from quant.market.fund_flow import (
    daily_net_yuan,
    intraday_big_net_yuan,
    resolve_main_net_yuan,
)
from quant.scoring.context import ScoreContext
from quant.scoring.dimensions.base import clamp
from quant.scoring.models import DimensionResult
from quant.timeutil import cn_today


def _float_market_cap_yuan(stock: dict) -> float | None:
    from quant.market.fund_flow import amount_to_yuan

    for key in ("流通市值", "总市值"):
        v = stock.get(key)
        if isinstance(v, (int, float)) and float(v) > 0:
            return float(v)
        yuan = amount_to_yuan(v) if v is not None else None
        if yuan and yuan > 0:
            return yuan
    return None


def _consecutive_outflow_days(daily: list[dict]) -> int:
    streak = 0
    for row in reversed(daily):
        if not isinstance(row, dict):
            break
        net = daily_net_yuan(row)
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


def score_stock_fund_flow(
    stock: dict,
    *,
    cfg: dict[str, Any] | None = None,
    mode: str = "",
    today_s: str = "",
) -> tuple[float, dict[str, Any]]:
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

    today = today_s or cn_today().isoformat()
    net_yuan, source = resolve_main_net_yuan(stock, mode=mode or "", today_s=today)
    if net_yuan is None:
        return neutral, {"available": False, "来源": "无数据"}

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
        "来源": source,
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
        cfg = (load_scoring_config().get("dimensions") or {}).get(self.name) or {}
        s, detail = score_stock_fund_flow(stock, cfg=cfg, mode=getattr(ctx, "mode", ""))
        available = bool(detail.get("available", True))
        if not available:
            detail = {}
        return DimensionResult(self.name, s, 0, True, available=available, detail=detail)
