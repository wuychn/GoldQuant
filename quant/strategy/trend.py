"""趋势（势）量化：买在向上/准备向上，卖在走弱/准备向下/向下。

价在 MA5/MA10 下方并不自动等于「势向下」——须结合结构、MA20 与动量。
"""

from __future__ import annotations

from typing import Any

from quant.config import load_gates_config
from quant.scoring.tech_indicators import hist_closes, ma_spread_pct, mas_from_stock
from quant.strategy.main_wave import (
    is_main_wave_acceleration,
    is_main_wave_pullback,
    is_main_wave_trend_active,
    is_spread_accelerating,
    is_trend_choppy,
    ma_bull_stack,
)

PHASE_UP = "向上"
PHASE_PREPARING_UP = "准备向上"
PHASE_WEAK = "走弱"
PHASE_PREPARING_DOWN = "准备向下"
PHASE_DOWN = "向下"

BUY_PHASES = frozenset({PHASE_UP, PHASE_PREPARING_UP})
SELL_PHASES = frozenset({PHASE_WEAK, PHASE_PREPARING_DOWN, PHASE_DOWN})


def _cfg(cfg: dict[str, Any] | None) -> dict[str, Any]:
    return cfg if cfg is not None else (load_gates_config().get("main_wave") or {})


def effective_ma20_break(last: float, ma20: float, cfg: dict[str, Any]) -> bool:
    """有效跌破 MA20：现价 < MA20 × trend_ma20_floor（默认约 1.5% 缓冲）。"""
    floor = float(cfg.get("trend_ma20_floor", 0.985))
    return ma20 > 0 and last < ma20 * floor


def quantify_trend(
    stock: dict,
    cfg: dict[str, Any] | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """返回 (阶段, 说明, 指标快照)。"""
    c = _cfg(cfg)
    m = mas_from_stock(stock)
    last = m.get("last")
    ma5, ma10, ma20 = m.get("ma5"), m.get("ma10"), m.get("ma20")
    detail: dict[str, Any] = {
        "现价": last,
        "MA5": ma5,
        "MA10": ma10,
        "MA20": ma20,
    }

    if not last or last <= 0:
        return PHASE_DOWN, "无有效现价", detail

    if is_trend_choppy(stock, c):
        return PHASE_DOWN, "震荡无序", detail

    ma20_floor = float(c.get("trend_ma20_floor", 0.985))
    ma5_break = float(c.get("ma5_break_ratio", 0.995))

    # --- 向下 / 准备向下（卖侧）---
    if ma20 and last < ma20 * ma20_floor:
        detail["有效破MA20"] = True
        return PHASE_DOWN, f"有效跌破MA20({ma20:.2f})", detail

    if ma5 and ma10 and ma5 < ma10:
        detail["MA5死叉MA10"] = True
        return PHASE_DOWN, "均线死叉，趋势向下", detail

    if ma5 and ma10 and ma20 and not ma_bull_stack(m):
        return PHASE_DOWN, "多头结构破坏", detail

    macd = m.get("macd")
    if macd is not None and macd < 0 and ma10 and last < ma10:
        detail["MACD弱"] = True
        return PHASE_PREPARING_DOWN, "MACD转弱且失守MA10", detail

    if ma5 and last < ma5 * ma5_break:
        return PHASE_PREPARING_DOWN, f"有效跌破MA5({ma5:.2f})", detail

    closes = hist_closes(stock.get("历史行情") or [])
    spread = ma_spread_pct(closes) if closes else None
    min_spread = float(c.get("min_ma_spread_pct", 0.8))
    if spread is not None and spread < min_spread * float(c.get("pullback_spread_ratio", 0.55)):
        if not is_spread_accelerating(closes, c):
            detail["发散收窄"] = round(spread, 2)
            return PHASE_WEAK, "均线发散收窄，动量走弱", detail

    # --- 向上 / 准备向上（买侧）---
    ok_accel, note_a = is_main_wave_acceleration(stock, c)
    if ok_accel:
        detail["加速"] = True
        return PHASE_UP, note_a, detail

    ok_pb, note_p = is_main_wave_pullback(stock, c)
    if ok_pb:
        detail["回调企稳区"] = True
        return PHASE_PREPARING_UP, note_p, detail

    ok_trend, note_t = is_main_wave_trend_active(stock, c)
    if ok_trend:
        detail["趋势有效"] = True
        return PHASE_PREPARING_UP, note_t, detail

    return PHASE_WEAK, "未满足主升趋势", detail


def trend_allows_buy(stock: dict, cfg: dict[str, Any] | None = None) -> tuple[bool, str]:
    phase, note, _ = quantify_trend(stock, cfg)
    if phase in BUY_PHASES:
        return True, note
    return False, f"趋势阶段[{phase}]：{note}"


def trend_allows_ascent_sell(stock: dict, cfg: dict[str, Any] | None = None) -> tuple[bool, str, str]:
    """上升途中仓：势走弱/准备向下/向下时允许趋势类卖出（不含止损）。"""
    phase, note, _ = quantify_trend(stock, cfg)
    if phase not in SELL_PHASES:
        return False, "", f"趋势阶段[{phase}]不卖"
    return True, phase, note
