"""单票日 K 序列上的策略判定。"""

from __future__ import annotations

from dataclasses import dataclass

from quant.tactic.limits import (
    close_limit_down,
    close_limit_up,
)


@dataclass
class Bar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    prev_close: float
    name: str = ""


def consecutive_close_limit_down(bars: list[Bar], code: str) -> bool:
    """窗口内任意相邻两日收盘跌停（不区分一字，只看收盘价）。"""
    for i in range(1, len(bars)):
        a, b = bars[i - 1], bars[i]
        if close_limit_down(a.close, a.prev_close, code, a.name) and close_limit_down(
            b.close, b.prev_close, code, b.name
        ):
            return True
    return False


def a_kill(bars: list[Bar]) -> bool:
    """窗口内任意「单日≤-9% 后次日继续创新低」。"""
    for i in range(1, len(bars)):
        a, b = bars[i - 1], bars[i]
        if a.prev_close <= 0:
            continue
        if a.close / a.prev_close - 1.0 <= -0.09 and b.low < a.low:
            return True
    return False


def ma5(bars: list[Bar]) -> float | None:
    if len(bars) < 5:
        return None
    return sum(x.close for x in bars[-5:]) / 5.0


def ma20(bars: list[Bar]) -> float | None:
    if len(bars) < 20:
        return None
    return sum(x.close for x in bars[-20:]) / 20.0


def rebound_higher_high(bars: list[Bar]) -> bool | None:
    """最近一次反弹高点是否高于前一次。不足两次反弹 → None（不强制）。"""
    if len(bars) < 5:
        return None
    swings: list[float] = []
    for i in range(1, len(bars) - 1):
        if bars[i].high >= bars[i - 1].high and bars[i].high > bars[i + 1].high:
            swings.append(bars[i].high)
    if len(swings) < 2:
        return None
    return swings[-1] > swings[-2]


def last_zt_streak(bars: list[Bar], code: str, *, min_n: int = 1) -> tuple[int, int, int]:
    """最近一段连续收盘涨停；(len, start, end)。``min_n``>1 时取最近一次达到该高度的连板。"""
    flags = [close_limit_up(b.close, b.prev_close, code, b.name) for b in bars]
    found = (0, -1, -1)
    i = 0
    while i < len(flags):
        if not flags[i]:
            i += 1
            continue
        j = i
        while j < len(flags) and flags[j]:
            j += 1
        n = j - i
        if n >= min_n:
            found = (n, i, j - 1)
        i = j
    return found


def natural_break_index(bars: list[Bar], code: str) -> int | None:
    """断板日下标（第 0 天）。收盘不再涨停即断板，含一字跌停。一字跌停本身不删票。"""
    n, start, end = last_zt_streak(bars, code, min_n=5)
    if n < 5 or end < 0:
        return None
    brk = end + 1
    if brk >= len(bars):
        return None
    b = bars[brk]
    if close_limit_up(b.close, b.prev_close, code, b.name):
        return None
    return brk
