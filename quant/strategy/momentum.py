"""近端动能：发散双窗口、价强豁免、高位衰减与动能分（0–100）。"""

from __future__ import annotations

from typing import Any

from quant.config import load_gates_config
from quant.scoring.tech_indicators import (
    hist_closes,
    hist_close,
    hist_open,
    hist_rows_sorted,
    ma_spread_pct,
    mas_from_stock,
    quote_avg_price,
    quote_change_pct,
    quote_last_price,
    to_float,
)


def _mw_cfg(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    return cfg if cfg is not None else (load_gates_config().get("main_wave") or {})


def _ma_bull_stack(m: dict[str, float | None]) -> bool:
    ma5, ma10, ma20 = m.get("ma5"), m.get("ma10"), m.get("ma20")
    if None in (ma5, ma10, ma20):
        return False
    return ma5 > ma10 > ma20  # type: ignore[operator]


def _net_return_pct(closes: list[float], days: int) -> float | None:
    if len(closes) <= days or days <= 0:
        return None
    start = closes[-days - 1]
    end = closes[-1]
    if start <= 0:
        return None
    return (end - start) / start * 100


def _spread_tail(closes: list[float], *, tail: int) -> list[float]:
    if len(closes) < 20:
        return []
    out: list[float] = []
    start = max(20, len(closes) - tail + 1)
    for end in range(start, len(closes) + 1):
        sp = ma_spread_pct(closes[:end])
        if sp is not None:
            out.append(sp)
    return out


def spread_accel_dual_window(closes: list[float], cfg: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """5 日发散扩张 + 近 N 日不能从峰值持续回落。"""
    min_spread = float(cfg.get("min_ma_spread_pct", 0.8))
    accel_delta = float(cfg.get("spread_accel_min_pct", 0.12))
    spread_days = int(cfg.get("spread_accel_days", 5))
    recent_days = int(cfg.get("spread_recent_days", 3))
    max_drop = float(cfg.get("spread_recent_max_drop_pct", 0.05))
    detail: dict[str, Any] = {}

    if len(closes) < 20 + spread_days:
        return False, detail

    spread_now = ma_spread_pct(closes)
    spread_prev = ma_spread_pct(closes[:-spread_days])
    if spread_now is None or spread_prev is None:
        return False, detail

    detail["发散5日"] = round(spread_now, 3)
    detail["发散5日前"] = round(spread_prev, 3)
    ok_5d = spread_now >= min_spread and spread_now >= spread_prev + accel_delta
    detail["发散5日扩张"] = ok_5d

    tail = _spread_tail(closes, tail=recent_days)
    ok_recent = True
    if len(tail) >= 2:
        peak = max(tail)
        ok_recent = spread_now >= peak - max_drop
        detail["发散近端峰值"] = round(peak, 3)
        detail["发散近端未回落"] = ok_recent

    return ok_5d and ok_recent, detail


def spread_accel_exempt(stock: dict, closes: list[float], cfg: dict[str, Any]) -> tuple[bool, str]:
    """发散未加速但价仍强：满足若干条仍视为加速段。"""
    min_signals = int(cfg.get("spread_exempt_min_signals", 2))
    min_recent_5d = float(cfg.get("momentum_recent_5d_min_pct", 8.0))
    high_days = int(cfg.get("momentum_high_break_days", 20))
    high_ratio = float(cfg.get("momentum_high_break_ratio", 0.97))
    min_day_chg = float(cfg.get("spread_exempt_day_chg_pct", 3.0))

    signals: list[str] = []
    last = quote_last_price(stock) or (closes[-1] if closes else None)

    net5 = _net_return_pct(closes, 5)
    if net5 is not None and net5 >= min_recent_5d:
        signals.append(f"近5日+{net5:.1f}%")

    if last and len(closes) >= high_days:
        tail = closes[-high_days:]
        if last >= max(tail) * high_ratio:
            signals.append(f"近{high_days}日新高")

    rows = hist_rows_sorted(stock.get("历史行情") or [])
    if len(rows) >= 3:
        up_days = 0
        for row in rows[-3:]:
            o, c = hist_open(row), hist_close(row)
            if o is not None and c is not None and c > o:
                up_days += 1
        if up_days >= 2:
            net3 = _net_return_pct(closes, 3)
            if net3 is not None and net3 > 0:
                signals.append("近3日阳线占优")

    day_chg = quote_change_pct(stock)
    if day_chg is not None and day_chg >= min_day_chg and _ma_bull_stack(mas_from_stock(stock)):
        signals.append(f"当日+{day_chg:.1f}%")

    if len(signals) >= min_signals:
        return True, "价强豁免(" + "、".join(signals[:3]) + ")"
    return False, ""


def is_spread_accelerating(closes: list[float], cfg: dict[str, Any]) -> bool:
    """双窗口发散加速（供 main_wave / choppy 豁免复用）。"""
    ok, _ = spread_accel_dual_window(closes, cfg)
    return ok


def momentum_fading_penalty(stock: dict, cfg: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """动能衰减与近端走弱软扣分。"""
    fading, _, detail = momentum_fading(stock, cfg)
    if not fading:
        return 0.0, {}
    per = float(cfg.get("momentum_fade_penalty_per_signal", 4.0))
    cap = float(cfg.get("momentum_fade_penalty_max", 12.0))
    n = int(detail.get("衰减信号数") or 0)
    p = min(cap, n * per)
    return p, {"动能衰减扣分": round(p, 1)}


def _hist_volumes(stock: dict) -> list[float]:
    vols: list[float] = []
    for row in hist_rows_sorted(stock.get("历史行情") or []):
        v = to_float(row.get("成交量") or row.get("volume"))
        if v is not None and v > 0:
            vols.append(v)
    return vols


def _session_high(stock: dict) -> float | None:
    pk = stock.get("盘口") if isinstance(stock.get("盘口"), dict) else {}
    high = to_float(pk.get("最高"))
    if high and high > 0:
        return high
    rows = hist_rows_sorted(stock.get("历史行情") or [])
    if rows:
        high = to_float(rows[-1].get("最高"))
        if high and high > 0:
            return high
    return None


def intraday_spike_fade(stock: dict, cfg: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    """日内冲高回落：自当日高点回撤≥阈值且现价低于均价。"""
    last = quote_last_price(stock)
    high = _session_high(stock)
    avg = quote_avg_price(stock)
    detail: dict[str, Any] = {}
    if not last or not high or high <= 0 or last <= 0:
        return False, "", detail

    fade_pct = (high - last) / high * 100
    min_fade = float(cfg.get("momentum_intraday_fade_min_pct", 5.0))
    require_below_avg = bool(cfg.get("momentum_intraday_require_below_avg", True))
    detail["日内高点"] = round(high, 4)
    detail["日内高点回撤_pct"] = round(fade_pct, 3)
    if avg and avg > 0:
        detail["均价"] = round(avg, 4)
        detail["低于均价"] = last < avg

    if fade_pct < min_fade:
        return False, "", detail
    if require_below_avg and avg and avg > 0 and last >= avg:
        return False, "", detail

    note = f"日内冲高回落(回撤{fade_pct:.1f}%"
    if avg and avg > 0:
        note += f"、低于均价{avg:.2f}"
    note += ")"
    return True, note, detail


def momentum_fading_signals(stock: dict, cfg: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """高位动能衰减信号列表。"""
    closes = hist_closes(stock.get("历史行情") or [])
    rows = hist_rows_sorted(stock.get("历史行情") or [])
    detail: dict[str, Any] = {}
    signals: list[str] = []

    intra_fade, intra_note, intra_detail = intraday_spike_fade(stock, cfg)
    detail.update(intra_detail)
    if intra_fade:
        signals.append(intra_note)

    if len(closes) < 20:
        return signals, detail

    spread_now = ma_spread_pct(closes)
    recent_days = int(cfg.get("spread_recent_days", 3))
    fade_spread_drop = float(cfg.get("momentum_fade_spread_drop_pct", 0.3))
    tail = _spread_tail(closes, tail=recent_days)
    if spread_now is not None and len(tail) >= 2:
        peak = max(tail)
        drop = peak - spread_now
        detail["发散近端回落"] = round(drop, 3)
        if drop >= fade_spread_drop:
            signals.append("发散近端回落")

    net5 = _net_return_pct(closes, 5)
    net10 = _net_return_pct(closes, 10)
    if net5 is not None and net10 is not None and net10 > 3.0:
        detail["近5日涨幅"] = round(net5, 2)
        detail["近10日涨幅"] = round(net10, 2)
        if net5 < net10 * 0.5:
            signals.append("涨速放缓")

    if len(rows) >= 13:
        bodies: list[float] = []
        for row in rows[-13:]:
            o, c = hist_open(row), hist_close(row)
            if o is not None and c is not None:
                bodies.append(abs(c - o))
        if len(bodies) >= 13:
            recent_avg = sum(bodies[-3:]) / 3
            base_avg = sum(bodies[-13:-3]) / 10
            detail["近3日实体均"] = round(recent_avg, 3)
            if base_avg > 0 and recent_avg < base_avg * float(cfg.get("momentum_fade_body_ratio", 0.6)):
                signals.append("K线实体缩小")

    vols = _hist_volumes(stock)
    if len(vols) >= 13:
        recent_vol = sum(vols[-3:]) / 3
        base_vol = sum(vols[-13:-3]) / 10
        detail["近3日量均"] = round(recent_vol, 0)
        if base_vol > 0 and recent_vol < base_vol * float(cfg.get("momentum_fade_volume_ratio", 0.8)):
            signals.append("量能萎缩")

    if len(closes) >= 5:
        recent_high = max(closes[-3:])
        prior_high = max(closes[-5:-2]) if len(closes) >= 5 else closes[-4]
        if recent_high <= prior_high:
            signals.append("近3日未创新高")

    return signals, detail


def momentum_fading(stock: dict, cfg: dict[str, Any] | None = None) -> tuple[bool, str, dict[str, Any]]:
    c = _mw_cfg(cfg)
    closes = hist_closes(stock.get("历史行情") or [])
    spread_ok, spread_detail = spread_accel_dual_window(closes, c)
    exempt = False
    if not spread_ok:
        exempt, _ = spread_accel_exempt(stock, closes, c)

    signals, detail = momentum_fading_signals(stock, c)
    detail.update(spread_detail)
    if exempt:
        detail["价强豁免"] = True

    # 日内冲高回落：单独即可判衰减（不受价强豁免中「近5日大涨」掩盖）
    intra_hit = any("日内冲高回落" in s for s in signals)
    if intra_hit:
        detail["衰减信号数"] = len(signals)
        fade_notes = [s for s in signals if "日内冲高回落" in s]
        others = [s for s in signals if s not in fade_notes]
        parts = fade_notes + others[:3]
        return True, "高位动能衰减：" + "、".join(parts), detail

    day_chg = quote_change_pct(stock) or 0.0
    net5 = _net_return_pct(closes, 5) or 0.0
    ignore_day = float(c.get("momentum_fade_ignore_day_chg_pct", 3.0))
    ignore_net5 = float(c.get("momentum_fade_ignore_net5_pct", 12.0))
    if exempt and (day_chg >= ignore_day or net5 >= ignore_net5):
        detail["衰减信号数"] = 0
        return False, "", detail

    # 高位钝化：近端发散回落 + 量缩/未创新高等
    if spread_detail.get("发散近端未回落") is False:
        plateau = [s for s in signals if s in ("量能萎缩", "近3日未创新高", "K线实体缩小")]
        if len(plateau) >= 2:
            detail["衰减信号数"] = len(signals)
            note = "高位动能衰减：" + "、".join(signals[:4])
            return True, note, detail

    need = int(c.get("momentum_fade_min_signals", 2))
    detail["衰减信号数"] = len(signals)
    if len(signals) >= need:
        return True, "高位动能衰减：" + "、".join(signals[:4]), detail
    return False, "", detail


def _clamp_score(v: float) -> float:
    return max(0.0, min(100.0, v))


def momentum_score(stock: dict, cfg: dict[str, Any] | None = None) -> tuple[float, dict[str, Any]]:
    """0–100 动能分，用于排序与主升维度微调。"""
    c = _mw_cfg(cfg)
    closes = hist_closes(stock.get("历史行情") or [])
    detail: dict[str, Any] = {}
    if len(closes) < 20:
        return 50.0, {"available": False}

    w = c.get("momentum_rank_weights") or {}
    w_ret = float(w.get("recent_return", 0.30))
    w_spread = float(w.get("spread_trend", 0.25))
    w_vol = float(w.get("volume_price", 0.20))
    w_struct = float(w.get("structure", 0.15))
    w_stab = float(w.get("stability", 0.10))

    # 近端涨幅
    net5 = _net_return_pct(closes, 5) or 0.0
    net10 = _net_return_pct(closes, 10) or 0.0
    ret_score = _clamp_score(40 + net5 * 2.5 + net10 * 0.8)
    detail["近5日涨幅"] = round(net5, 2)
    detail["近10日涨幅"] = round(net10, 2)

    spread_ok, spread_detail = spread_accel_dual_window(closes, c)
    detail.update(spread_detail)
    exempt, ex_note = (False, "")
    if not spread_ok:
        exempt, ex_note = spread_accel_exempt(stock, closes, c)
    spread_score = 85.0 if spread_ok else (72.0 if exempt else 35.0)
    if ex_note:
        detail["价强豁免"] = ex_note

    # 量价：涨时量不显著萎缩
    vols = _hist_volumes(stock)
    vol_score = 55.0
    if len(vols) >= 10 and net5 > 0:
        recent = sum(vols[-3:]) / 3
        base = sum(vols[-10:-3]) / 7
        if base > 0:
            ratio = recent / base
            vol_score = _clamp_score(40 + ratio * 35)
            detail["量比近均"] = round(ratio, 2)

    m = mas_from_stock(stock)
    struct_score = 50.0
    if _ma_bull_stack(m):
        struct_score += 25
    last = m.get("last") or closes[-1]
    ma20 = m.get("ma20")
    if ma20 and last >= ma20:
        struct_score += min(25, (last / ma20 - 1) * 200)
    high_days = int(c.get("momentum_high_break_days", 20))
    if len(closes) >= high_days and last >= max(closes[-high_days:]) * float(
        c.get("momentum_high_break_ratio", 0.97)
    ):
        struct_score += 10
    struct_score = _clamp_score(struct_score)

    fade, fade_note, fade_detail = momentum_fading(stock, c)
    detail.update(fade_detail)
    stab_score = 70.0
    if fade:
        stab_score = max(15.0, 70.0 - 12.0 * len(momentum_fading_signals(stock, c)[0]))
        detail["衰减"] = fade_note

    total = (
        ret_score * w_ret
        + spread_score * w_spread
        + vol_score * w_vol
        + struct_score * w_struct
        + stab_score * w_stab
    )
    intra_fade, _, _ = intraday_spike_fade(stock, c)
    if intra_fade:
        pen = float(c.get("momentum_intraday_fade_score_penalty", 22.0))
        total -= pen
        detail["日内动能扣分"] = pen

    detail["动能分"] = round(_clamp_score(total), 1)
    return _clamp_score(total), detail


def momentum_buy_floor(cfg: dict[str, Any] | None = None) -> float:
    c = _mw_cfg(cfg)
    return float(c.get("momentum_buy_min_score", 45.0))
