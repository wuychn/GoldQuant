"""主升浪战法：加速段 + 趋势内健康回调 + 买点/卖点判定。

- 加速段：发散扩大、贴 MA5 上行（吃确定性最强的一段）
- 趋势内回调：多头结构未破、回踩 MA10~MA20，仍算主升波段（不因短期回调剔除）
- 排除几个月上蹿下跳的震荡票
"""

from __future__ import annotations

from typing import Any

from quant.config import load_gates_config
from quant.scoring.context import ScoreContext
from quant.scoring.tech_indicators import (
    hist_closes,
    hist_daily_changes,
    ma_spread_pct,
    mas_from_stock,
    quote_avg_price,
    quote_change_pct,
    quote_open_price,
)

PHASE_ACCEL = "加速"
PHASE_PULLBACK = "回调"
PHASE_TREND = "趋势"


def _mas(stock: dict) -> dict[str, float | None]:
    return mas_from_stock(stock)


def _mw_cfg(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    return cfg if cfg is not None else (load_gates_config().get("main_wave") or {})


def ma_bull_stack(m: dict[str, float | None]) -> bool:
    ma5, ma10, ma20 = m.get("ma5"), m.get("ma10"), m.get("ma20")
    if None in (ma5, ma10, ma20):
        return False
    return ma5 > ma10 > ma20  # type: ignore[operator]


def ma_diverging(m: dict[str, float | None], *, min_spread_pct: float) -> bool:
    ma5, ma10, ma20 = m.get("ma5"), m.get("ma10"), m.get("ma20")
    if None in (ma5, ma10, ma20) or ma20 <= 0:
        return False
    if not (ma5 > ma10 > ma20):
        return False
    spread = (ma5 - ma20) / ma20 * 100
    mid_spread = (ma10 - ma20) / ma20 * 100
    return spread >= min_spread_pct and mid_spread >= min_spread_pct * 0.5


def _peak_spread_pct(closes: list[float], window: int) -> float | None:
    if len(closes) < 20:
        return None
    peak: float | None = None
    tail = min(window, len(closes) - 19)
    for i in range(tail):
        end = len(closes) - i
        if end < 20:
            break
        sp = ma_spread_pct(closes[:end])
        if sp is not None:
            peak = sp if peak is None else max(peak, sp)
    return peak


def is_spread_accelerating(closes: list[float], cfg: dict[str, Any]) -> bool:
    """发散度在近若干日明显扩大。"""
    min_spread = float(cfg.get("min_ma_spread_pct", 0.8))
    accel_delta = float(cfg.get("spread_accel_min_pct", 0.12))
    spread_days = int(cfg.get("spread_accel_days", 5))
    if len(closes) < 20 + spread_days:
        return False
    spread_now = ma_spread_pct(closes)
    spread_prev = ma_spread_pct(closes[:-spread_days])
    if spread_now is None or spread_prev is None:
        return False
    return spread_now >= min_spread and spread_now >= spread_prev + accel_delta


def is_trend_choppy(stock: dict, cfg: dict[str, Any]) -> bool:
    """几个月上蹿下跳：路径远大于净涨幅，或涨跌频繁反转。"""
    lookback = int(cfg.get("choppy_lookback_days", 60))
    max_path_ratio = float(cfg.get("choppy_path_ratio", 3.5))
    max_flip_rate = float(cfg.get("choppy_flip_rate", 0.42))
    min_net_pct = float(cfg.get("choppy_min_net_pct", 5.0))

    changes = hist_daily_changes(stock.get("历史行情") or [])
    closes = hist_closes(stock.get("历史行情") or [])
    if len(changes) < 20 or len(closes) < lookback:
        return False

    window = changes[-lookback:]
    start = closes[-lookback]
    end = closes[-1]
    if start <= 0:
        return False
    net = (end - start) / start * 100
    if net < min_net_pct:
        return True

    path = sum(abs(c) for c in window)
    path_ratio = path / max(abs(net), 1.0)
    if path_ratio > max_path_ratio:
        return True

    flips = sum(1 for i in range(1, len(window)) if window[i] * window[i - 1] < 0)
    flip_rate = flips / max(len(window) - 1, 1)
    return flip_rate > max_flip_rate


def is_main_wave_trend_active(stock: dict, cfg: dict[str, Any] | None = None) -> tuple[bool, str]:
    """主升趋势仍有效：多头结构 + 未破 MA20 + 近期曾发散（允许回调期 spread 略收窄）。"""
    c = _mw_cfg(cfg)
    m = _mas(stock)
    last = m.get("last")
    if not last or last <= 0:
        return False, "无有效现价"
    if not ma_bull_stack(m):
        return False, "均线非多头"
    if is_trend_choppy(stock, c):
        return False, "震荡上蹿下跳"

    ma20 = m.get("ma20")
    floor = float(c.get("trend_ma20_floor", 0.985))
    if ma20 and last < ma20 * floor:
        return False, "有效跌破MA20"

    min_spread = float(c.get("min_ma_spread_pct", 0.8))
    relax = float(c.get("pullback_spread_ratio", 0.55))
    closes = hist_closes(stock.get("历史行情") or [])
    spread_now = ma_spread_pct(closes) if closes else None
    peak_window = int(c.get("trend_peak_spread_days", 15))
    peak = _peak_spread_pct(closes, peak_window)

    spread_ok = False
    if spread_now is not None and spread_now >= min_spread * relax:
        spread_ok = True
    if peak is not None and peak >= min_spread:
        spread_ok = True
    if not spread_ok:
        return False, "发散结构失效"

    return True, "主升趋势有效"


def is_main_wave_acceleration(
    stock: dict,
    cfg: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """加速段：趋势有效 + 发散扩大 + 价贴 MA5 上行。"""
    c = _mw_cfg(cfg)
    ok_trend, msg = is_main_wave_trend_active(stock, c)
    if not ok_trend:
        return False, msg

    m = _mas(stock)
    last = m.get("last")
    closes = hist_closes(stock.get("历史行情") or [])
    if not is_spread_accelerating(closes, c):
        return False, "发散未加速"

    ma5 = m.get("ma5")
    if ma5 and len(closes) >= 10:
        ma5_prev = sum(closes[-10:-5]) / 5
        if ma5_prev > 0 and ma5 <= ma5_prev:
            return False, "MA5未继续抬高"

    floor = float(c.get("accel_price_ma5_ratio", 0.97))
    if ma5 and last < ma5 * floor:
        return False, "远离加速均线"

    return True, "主升浪加速段"


def is_main_wave_pullback(stock: dict, cfg: dict[str, Any] | None = None) -> tuple[bool, str]:
    """趋势内健康回调：未破 MA20、回踩 MA10 一带，结构仍多头。"""
    c = _mw_cfg(cfg)
    ok_trend, msg = is_main_wave_trend_active(stock, c)
    if not ok_trend:
        return False, msg

    m = _mas(stock)
    last = m.get("last")
    ma5, ma10, ma20 = m.get("ma5"), m.get("ma10"), m.get("ma20")
    if not last or not ma10 or not ma20:
        return False, "均线数据不足"

    lo = float(c.get("pullback_ma_zone_low", 0.985))
    hi = float(c.get("pullback_ma_zone_high", 1.015))
    zone_low = ma10 * lo
    zone_high = ma10 * hi

    in_zone = zone_low <= last <= zone_high or (ma20 <= last <= ma10 * 1.02)
    if not in_zone:
        return False, "不在回调均线区"

    if ma5 and last > ma5 * float(c.get("pullback_above_ma5_ratio", 1.02)):
        return False, "仍贴MA5上行属加速段"

    return True, "主升趋势内回调"


def main_wave_phase(
    stock: dict,
    cfg: dict[str, Any] | None = None,
) -> tuple[bool, str, str]:
    """返回 (是否在主升波段, 阶段, 说明)。阶段：加速 / 回调 / 趋势。"""
    c = _mw_cfg(cfg)
    ok_accel, note_a = is_main_wave_acceleration(stock, c)
    if ok_accel:
        return True, PHASE_ACCEL, note_a
    ok_pb, note_p = is_main_wave_pullback(stock, c)
    if ok_pb:
        return True, PHASE_PULLBACK, note_p
    ok_t, note_t = is_main_wave_trend_active(stock, c)
    if ok_t:
        return True, PHASE_TREND, note_t
    return False, "", note_a or note_p or note_t


def is_in_main_wave(stock: dict, cfg: dict[str, Any] | None = None) -> tuple[bool, str]:
    """候选池/买入前置：加速或趋势内回调均保留，不因短期回调剔除。"""
    ok, phase, note = main_wave_phase(stock, cfg)
    if not ok:
        return False, note
    if phase == PHASE_PULLBACK:
        return True, "主升趋势内回调"
    if phase == PHASE_ACCEL:
        return True, "主升加速段"
    return True, "主升趋势中"


def detect_buy_setup(
    stock: dict,
    ctx: ScoreContext,
    cfg: dict[str, Any],
) -> tuple[bool, str, str]:
    """买点：主升波段内，上升途中或回调企稳。"""
    del ctx
    ok, phase, reason = main_wave_phase(stock, cfg)
    if not ok:
        return False, "", reason

    m = _mas(stock)
    last = m.get("last")
    if not last:
        return False, "", "无有效现价"

    min_spread = float(cfg.get("min_ma_spread_pct", 0.8))
    lo = float(cfg.get("pullback_ma_zone_low", 0.985))
    hi = float(cfg.get("pullback_ma_zone_high", 1.015))

    if phase in (PHASE_ACCEL, PHASE_TREND) and ma_diverging(m, min_spread_pct=min_spread):
        ma5 = m.get("ma5")
        if ma5 and last >= ma5:
            avg = quote_avg_price(stock) or 0.0
            if avg <= 0 or last > avg:
                from quant.constants import BUY_KIND_ASCENT

                return True, BUY_KIND_ASCENT, "主升波段上升途中"

    ma10, ma20 = m.get("ma10"), m.get("ma20")
    if ma10 and ma20 and last >= ma20 * 0.995:
        zone_low = ma10 * lo
        zone_high = ma10 * hi
        if zone_low <= last <= zone_high or (ma20 <= last <= ma10 * 1.01):
            chg = quote_change_pct(stock)
            open_p = quote_open_price(stock, fallback=last) or last
            avg = quote_avg_price(stock)
            above_avg = avg is None or avg <= 0 or last > avg
            if above_avg and (last >= open_p or (chg is not None and chg >= -1.0)):
                from quant.constants import BUY_KIND_PULLBACK

                return True, BUY_KIND_PULLBACK, "主升波段回调至均线区企稳"

    return False, "", "波段内未触发买点"


def detect_sell_setup(
    stock: dict,
    ctx: ScoreContext,
    cfg: dict[str, Any],
) -> tuple[bool, str, str]:
    """卖点分型：破5日线（当日） vs 趋势衰竭（可跨日）。"""
    del ctx
    from quant.constants import SELL_KIND_MA5_BREAK, SELL_KIND_TREND_ERODE

    m = _mas(stock)
    last = m.get("last")
    ma5, ma10, ma20 = m.get("ma5"), m.get("ma10"), m.get("ma20")
    if not last:
        return False, "", ""

    ma5_ratio = float(cfg.get("ma5_break_ratio", 0.995))
    ma20_ratio = float(cfg.get("ma20_break_ratio", 0.995))

    if ma5 and last < ma5 * ma5_ratio:
        return True, SELL_KIND_MA5_BREAK, f"有效跌破MA5({ma5:.2f})"

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
