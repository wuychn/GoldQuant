"""收盘涨跌停与板块分档（策略用收盘触及，不要求全天封死）。"""

from __future__ import annotations

import math

PRICE_TOL = 0.005


def _f(v) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return float("nan")
    return x if math.isfinite(x) else float("nan")


def board_limit_pct(code: str, name: str | None = None) -> float:
    c = str(code or "").strip().zfill(6)
    if c.startswith(("300", "688")):
        return 0.20
    if c.startswith(("92", "83", "87", "88", "43")) or c.startswith("8"):
        return 0.30
    if name and any(m in str(name).upper() for m in ("ST", "*ST")):
        return 0.05
    return 0.10


def is_chi_next(code: str) -> bool:
    c = str(code or "").strip().zfill(6)
    return c.startswith(("300", "688"))


def limit_up_px(prev: float, code: str, name: str | None = None) -> float:
    return round(float(prev) * (1.0 + board_limit_pct(code, name)), 2)


def limit_down_px(prev: float, code: str, name: str | None = None) -> float:
    return round(float(prev) * (1.0 - board_limit_pct(code, name)), 2)


def close_limit_up(close: float, prev: float, code: str, name: str | None = None) -> bool:
    if prev is None or prev <= 0:
        return False
    c = _f(close)
    if math.isnan(c) or c <= 0:
        return False
    return c >= limit_up_px(prev, code, name) - PRICE_TOL


def close_limit_down(close: float, prev: float, code: str, name: str | None = None) -> bool:
    if prev is None or prev <= 0:
        return False
    c = _f(close)
    if math.isnan(c) or c <= 0:
        return False
    return c <= limit_down_px(prev, code, name) + PRICE_TOL


def is_one_word_limit_down(
    open_: float,
    high: float,
    low: float,
    close: float,
    prev: float,
    code: str,
    name: str | None = None,
) -> bool:
    """一字跌停：开高低收都在跌停价。"""
    if prev is None or prev <= 0:
        return False
    down = limit_down_px(prev, code, name)
    vals = [_f(open_), _f(high), _f(low), _f(close)]
    if any(math.isnan(x) or x <= 0 for x in vals):
        return False
    return all(x <= down + PRICE_TOL for x in vals)


def is_yang(open_: float, close: float) -> bool:
    return _f(close) > _f(open_)


def is_fake_yin(open_: float, close: float, prev: float) -> bool:
    """假阴：收盘低于开盘，但高于昨收。"""
    o, c, p = _f(open_), _f(close), _f(prev)
    if any(math.isnan(x) for x in (o, c, p)):
        return False
    return c < o and c > p


def is_long_lower_shadow(open_: float, high: float, low: float, close: float) -> bool:
    o, h, lo, c = _f(open_), _f(high), _f(low), _f(close)
    if any(math.isnan(x) for x in (o, h, lo, c)):
        return False
    body = abs(c - o)
    lower = min(o, c) - lo
    if body <= PRICE_TOL:
        return lower >= 2 * max(h - lo, PRICE_TOL)
    return lower >= 2 * body


def is_stop_k(open_: float, high: float, low: float, close: float, prev: float) -> bool:
    return (
        is_yang(open_, close)
        or is_fake_yin(open_, close, prev)
        or is_long_lower_shadow(open_, high, low, close)
    )


def pullback_cap(code: str) -> float:
    """模式A 调整幅度上限。超过则删除。主板 20%，双创 30%。"""
    return 0.30 if is_chi_next(code) else 0.20


def chop_cap(code: str) -> float:
    """模式B 横盘回撤上限。超过则删除。主板 15%，双创 20%。"""
    return 0.20 if is_chi_next(code) else 0.15
