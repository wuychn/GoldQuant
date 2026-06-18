"""买入阈值与追高线：强市 / 买点类型动态放宽。"""

from __future__ import annotations

from quant.constants import BUY_KIND_ASCENT
from quant.scoring.context import index_change, profit_effect


def is_strong_market(payload: dict, buy_cfg: dict | None = None) -> bool:
    """强市判定（独立于 infer_regime 仓位档位，用于买入放宽）。"""
    cfg = buy_cfg or {}
    sm = cfg.get("strong_market") or {}
    if not sm.get("enabled", True):
        return False

    idx_min = float(sm.get("index_min_pct", 1.0))
    zt_min = int(sm.get("zt_min", 80))
    require_up_gt_down = bool(sm.get("require_up_gt_down", True))

    profit = profit_effect(payload)
    up = int(profit.get("上涨", 0) or 0)
    down = int(profit.get("下跌", 0) or 0)
    zt = int(profit.get("涨停", 0) or 0)
    idx = index_change(payload)

    if idx is None or idx < idx_min:
        return False
    if zt < zt_min:
        return False
    if require_up_gt_down and up <= down:
        return False
    return True


def effective_buy_threshold(base: float, payload: dict, buy_cfg: dict) -> float:
    sm = buy_cfg.get("strong_market") or {}
    if is_strong_market(payload, buy_cfg):
        delta = float(sm.get("buy_threshold_delta", -2))
        return max(50.0, base + delta)
    return base


def effective_max_change_pct(
    buy_cfg: dict,
    payload: dict,
    *,
    buy_kind: str = "",
    score: float = 0.0,
) -> float:
    by_kind = buy_cfg.get("max_change_by_kind") or {}
    if buy_kind and buy_kind in by_kind:
        base = float(by_kind[buy_kind])
    else:
        base = float(buy_cfg.get("max_change_pct", 8.0))
    sm = buy_cfg.get("strong_market") or {}
    if is_strong_market(payload, buy_cfg):
        base = max(base, float(sm.get("max_change_pct", base)))
    if buy_kind == BUY_KIND_ASCENT and score >= float(buy_cfg.get("ascent_high_score", 78)):
        base = max(base, float(buy_cfg.get("ascent_high_score_max_chg", 9.5)))
    return base
