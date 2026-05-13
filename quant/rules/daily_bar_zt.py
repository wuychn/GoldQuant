"""日线是否涨停（用于连板统计等），阈值随代码板块可配。"""

from __future__ import annotations

from typing import Any


def zt_threshold_pct(code: str, *, main_pct: float = 9.8, cyb_pct: float = 19.8) -> float:
    c = str(code or "").strip()
    return cyb_pct if c.startswith("30") else main_pct


def bar_is_limit_up(
    bar: dict[str, Any],
    prev_bar: dict[str, Any] | None,
    code: str,
    *,
    main_pct: float = 9.8,
    cyb_pct: float = 19.8,
) -> bool:
    """单日是否触及涨停阈值（有涨跌幅字段优先，否则用相邻收盘）。"""
    th = zt_threshold_pct(code, main_pct=main_pct, cyb_pct=cyb_pct)
    ch = bar.get("涨跌幅")
    if ch is not None:
        try:
            return float(ch) >= th
        except (TypeError, ValueError):
            pass
    if prev_bar is None:
        return False
    try:
        cur_close = float(bar.get("收盘", 0))
        prev_close = float(prev_bar.get("收盘", 0))
        if prev_close <= 0:
            return False
        pct = (cur_close - prev_close) / prev_close * 100
        return pct >= th
    except (TypeError, ValueError):
        return False


def max_consecutive_limit_up_days(
    history: list[Any],
    code: str,
    *,
    lookback_days: int,
    main_pct: float = 9.8,
    cyb_pct: float = 19.8,
) -> int:
    """在最近 lookback_days 根 K 线（含当日）内，统计「涨停」连板的最大连续天数。"""
    if not history or not isinstance(history, list) or len(history) < 2:
        return 0
    start = max(1, len(history) - lookback_days)
    best = cur = 0
    for i in range(start, len(history)):
        bar = history[i]
        prev = history[i - 1]
        if not isinstance(bar, dict) or not isinstance(prev, dict):
            cur = 0
            continue
        if bar_is_limit_up(bar, prev, code, main_pct=main_pct, cyb_pct=cyb_pct):
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best
