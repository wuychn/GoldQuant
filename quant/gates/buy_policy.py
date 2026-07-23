"""买入阈值与追高线：强/弱市、买点类型、近端涨幅条件过滤。"""

from __future__ import annotations

from quant.constants import BUY_KIND_ASCENT
from quant.scoring.context import index_change, infer_regime, profit_effect
from quant.scoring.models import StockScore
from quant.scoring.tech_indicators import hist_closes, quote_last_price
from quant.strategy.momentum import momentum_fading


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


def market_up_down_ratio(payload: dict) -> float | None:
    profit = profit_effect(payload)
    up = int(profit.get("上涨", 0) or 0)
    down = int(profit.get("下跌", 0) or 0)
    if down <= 0:
        return None if up <= 0 else float("inf")
    return up / down


def market_allows_new_buy(payload: dict, buy_cfg: dict | None = None) -> tuple[bool, str]:
    """全局：极端弱势或涨少跌多时暂停新开仓。"""
    cfg = buy_cfg or {}
    wm = cfg.get("weak_market") or {}
    if not wm.get("enabled", True):
        return True, ""

    profit = profit_effect(payload)
    if wm.get("treat_null_profit_as_weak", True) and not profit:
        return True, ""

    ratio = market_up_down_ratio(payload)
    ratio_min = float(wm.get("up_down_ratio_min", 0.25))
    if ratio is not None and ratio < ratio_min and wm.get("block_new_buys", True):
        up = int(profit.get("上涨", 0) or 0)
        down = int(profit.get("下跌", 0) or 0)
        return False, f"上涨/下跌={up}/{down}，赚钱效应偏弱"

    if infer_regime(payload) == "弱势" and wm.get("block_new_buys", True):
        return False, "市场档位偏弱，暂停新开仓"
    return True, ""


def effective_buy_threshold(base: float, payload: dict, buy_cfg: dict) -> float:
    threshold = float(base)
    wm = buy_cfg.get("weak_market") or {}
    if wm.get("enabled", True):
        profit = profit_effect(payload)
        if wm.get("treat_null_profit_as_weak", True) and not profit:
            threshold += float(wm.get("null_profit_threshold_delta", 2))
        elif infer_regime(payload) == "弱势":
            threshold += float(wm.get("buy_threshold_delta", 3))

    sm = buy_cfg.get("strong_market") or {}
    if is_strong_market(payload, buy_cfg):
        delta = float(sm.get("buy_threshold_delta", -2))
        threshold = max(50.0, threshold + delta)
    return threshold


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


def recent_gain_pct(stock: dict, *, days: int = 3) -> float | None:
    """近 N 个交易日涨幅(%)：起点为 N 日前收盘，终点优先现价。"""
    closes = hist_closes(stock.get("历史行情") or [])
    if len(closes) < 2:
        return None
    if len(closes) <= days:
        start = closes[0]
    else:
        start = closes[-days - 1]
    end = quote_last_price(stock) or closes[-1]
    if start <= 0 or end <= 0:
        return None
    return (end - start) / start * 100


def recent_gain_guard_allows(
    stock: dict,
    payload: dict,
    buy_cfg: dict,
    *,
    buy_kind: str,
    score: float,
    mw_cfg: dict | None = None,
) -> tuple[bool, str]:
    """近端涨幅条件过滤：涨多了不硬禁，高分且无动能衰减可豁免（强者恒强）。"""
    guard = buy_cfg.get("recent_gain_guard") or {}
    if not guard.get("enabled", True):
        return True, ""

    kinds = guard.get("apply_to_kinds") or [BUY_KIND_ASCENT]
    if buy_kind not in kinds:
        return True, ""

    lookback = int(guard.get("lookback_days", 3))
    ret = recent_gain_pct(stock, days=lookback)
    if ret is None:
        return True, ""

    regime = infer_regime(payload)
    limits = guard.get("limits") or {}
    limit = float(limits.get(regime, limits.get("震荡", 18.0)))
    if ret <= limit:
        return True, ""

    exempt_score = float(guard.get("exempt_min_score", 80))
    if score >= exempt_score:
        if guard.get("exempt_require_no_momentum_decay", True):
            fading, _, _ = momentum_fading(stock, mw_cfg)
            if not fading:
                return True, ""
        else:
            return True, ""

    return False, (
        f"近{lookback}日涨幅{ret:.1f}%超{regime}上限{limit:.0f}%"
        f"且未达高分豁免(≥{exempt_score:.0f}分且无动能衰减)"
    )


def concept_theme_dimension_score(score: StockScore) -> float | None:
    for dim in score.dimensions:
        if dim.name == "concept_theme" and dim.available:
            return dim.score
    return None


def concept_theme_allows_buy(score: StockScore, buy_cfg: dict) -> tuple[bool, str]:
    min_score = float(buy_cfg.get("concept_theme_min_score", 0))
    if min_score <= 0:
        return True, ""
    ct = concept_theme_dimension_score(score)
    if ct is None:
        return True, ""
    if ct >= min_score:
        return True, ""
    return False, f"概念共振{ct:.1f}<{min_score:.0f}"


def buy_kind_allowed_in_regime(buy_kind: str, payload: dict, buy_cfg: dict) -> tuple[bool, str]:
    if buy_kind != BUY_KIND_ASCENT:
        return True, ""
    if not buy_cfg.get("block_ascent_in_weak_regime", True):
        return True, ""
    regime = infer_regime(payload)
    if regime == "弱势":
        return False, "弱势档禁止「上升途中」追涨，仅允许回调企稳"
    profit = profit_effect(payload)
    if buy_cfg.get("block_ascent_when_profit_null", True) and not profit:
        return False, "赚钱效应缺失，禁止「上升途中」追涨"
    return True, ""

