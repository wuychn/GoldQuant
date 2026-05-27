"""主升浪战法：主线龙头 + 均线发散向上 + 买点/卖点判定。

仅做主升浪，不做涨停板/龙回头。龙头定义：所属概念命中主线榜 + 人气/涨幅靠前。
"""

from __future__ import annotations

from typing import Any

from quant.scoring.context import ScoreContext
from quant.scoring.dimensions.concept_theme import _stock_concepts, attach_concepts_from_hot
from quant.scoring.theme_tracker import resolve_main_themes


def _f(v: object, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _mas(stock: dict) -> dict[str, float | None]:
    t = stock.get("技术指标") if isinstance(stock.get("技术指标"), dict) else {}
    pk = stock.get("盘口") if isinstance(stock.get("盘口"), dict) else {}
    return {
        "ma5": _f(t.get("MA5")) if t.get("MA5") is not None else None,
        "ma10": _f(t.get("MA10")) if t.get("MA10") is not None else None,
        "ma20": _f(t.get("MA20")) if t.get("MA20") is not None else None,
        "last": _f(pk.get("最新")) if pk.get("最新") is not None else None,
        "macd": _f(t.get("MACD")) if t.get("MACD") is not None else None,
    }


def ma_bull_stack(m: dict[str, float | None]) -> bool:
    ma5, ma10, ma20 = m.get("ma5"), m.get("ma10"), m.get("ma20")
    if None in (ma5, ma10, ma20):
        return False
    return ma5 > ma10 > ma20  # type: ignore[operator]


def ma_diverging(m: dict[str, float | None], *, min_spread_pct: float) -> bool:
    """均线发散：MA5 与 MA20 间距扩大且多头。"""
    ma5, ma10, ma20 = m.get("ma5"), m.get("ma10"), m.get("ma20")
    if None in (ma5, ma10, ma20) or ma20 <= 0:
        return False
    if not (ma5 > ma10 > ma20):
        return False
    spread = (ma5 - ma20) / ma20 * 100
    mid_spread = (ma10 - ma20) / ma20 * 100
    return spread >= min_spread_pct and mid_spread >= min_spread_pct * 0.5


def is_theme_leader(stock: dict, ctx: ScoreContext, *, max_rank: int) -> bool:
    """主线题材中的龙头：概念共振 + 人气排名靠前。"""
    stock = attach_concepts_from_hot(stock, ctx.payload)
    tops = resolve_main_themes(ctx.payload)
    concepts = _stock_concepts(stock)
    if not tops or not (concepts & tops):
        return False
    rank = stock.get("人气排名")
    if rank is not None:
        try:
            return int(rank) <= max_rank
        except (TypeError, ValueError):
            pass
    code = str(stock.get("股票代码", "")).strip()
    for row in ctx.payload.get("同花顺人气榜") or []:
        if str(row.get("股票代码", "")).strip() != code:
            continue
        try:
            return int(row.get("人气排名", 999)) <= max_rank
        except (TypeError, ValueError):
            break
    # 无排名时：只要在主线概念且均线多头，放宽为候选
    return ma_bull_stack(_mas(stock))


def detect_buy_setup(
    stock: dict,
    ctx: ScoreContext,
    cfg: dict[str, Any],
) -> tuple[bool, str, str]:
    """返回 (是否触发, signal_kind, 原因)。"""
    m = _mas(stock)
    last = m.get("last")
    if not last or last <= 0:
        return False, "", "无有效现价"

    min_spread = float(cfg.get("min_ma_spread_pct", 0.8))
    max_rank = int(cfg.get("leader_max_rank", 15))
    if not is_theme_leader(stock, ctx, max_rank=max_rank):
        return False, "", "非主线龙头"

    if not ma_bull_stack(m):
        return False, "", "均线非多头发散"

    pk = stock.get("盘口") if isinstance(stock.get("盘口"), dict) else {}
    lo = float(cfg.get("pullback_ma_zone_low", 0.985))
    hi = float(cfg.get("pullback_ma_zone_high", 1.015))

    # 上升途中：均线发散 + 价在 MA5 上方 + 分时强势
    if ma_diverging(m, min_spread_pct=min_spread):
        ma5 = m.get("ma5")
        if ma5 and last >= ma5:
            avg = _f(pk.get("均价"))
            if avg <= 0 or last >= avg:
                from quant.constants import BUY_KIND_ASCENT

                return True, BUY_KIND_ASCENT, "主线龙头均线发散向上，上升途中"

    # 回调企稳：趋势未破 MA20，回踩 MA10~MA20 区间后企稳
    ma10, ma20 = m.get("ma10"), m.get("ma20")
    if ma10 and ma20 and last >= ma20 * 0.995:
        zone_low = ma10 * lo
        zone_high = ma10 * hi
        if zone_low <= last <= zone_high or (ma20 <= last <= ma10 * 1.01):
            chg = _f(pk.get("涨幅"))
            if last >= _f(pk.get("今开", last)) or chg >= -1.5:
                from quant.constants import BUY_KIND_PULLBACK

                return True, BUY_KIND_PULLBACK, "主线龙头回调至均线区企稳"

    return False, "", "未满足主升浪买点"


def detect_sell_setup(
    stock: dict,
    ctx: ScoreContext,
    cfg: dict[str, Any],
) -> tuple[bool, str, str]:
    """卖点分型：破5日线（当日） vs 趋势衰竭（可跨日）。"""
    from quant.constants import SELL_KIND_MA5_BREAK, SELL_KIND_TREND_ERODE

    m = _mas(stock)
    last = m.get("last")
    ma5, ma10, ma20 = m.get("ma5"), m.get("ma10"), m.get("ma20")
    if not last:
        return False, "", ""

    ma5_ratio = float(cfg.get("ma5_break_ratio", 0.995))
    ma20_ratio = float(cfg.get("ma20_break_ratio", 0.995))

    # 1) 破5日线：当日有效跌破 MA5（独立三确认链）
    if ma5 and last < ma5 * ma5_ratio:
        return True, SELL_KIND_MA5_BREAK, f"有效跌破MA5({ma5:.2f})"

    # 2) 趋势衰竭：结构逐步转弱，可在多日 during 中累计确认
    reasons: list[str] = []
    if ma5 and ma10 and ma5 < ma10:
        reasons.append("MA5下穿MA10")
    if ma5 and ma10 and ma20 and not (ma5 > ma10 > ma20):
        reasons.append("均线多头结构破坏")
    if ma20 and last < ma20 * ma20_ratio:
        reasons.append(f"跌破MA20({ma20:.2f})")
    macd = m.get("macd")
    if macd is not None and macd < 0 and ma10 and last < ma10:
        reasons.append("MACD转弱且失守MA10")

    if reasons:
        return True, SELL_KIND_TREND_ERODE, "；".join(reasons)

    return False, "", ""
