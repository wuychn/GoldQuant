"""可交易性判定：停牌 + 涨跌停 + T+1。

回测中用当日 OHLC 推断：
- 停牌：当日 high==low==close==0 或 volume==0
- 一字涨停：open==high==low==close 且 close >= prev_close*1.097
- 一字跌停：同上且 close <= prev_close*0.907
- 涨停封死：买不进；跌停封死：卖不出
- T+1：当日买入次日才可卖
"""

from __future__ import annotations

import numpy as np


def is_suspended(row: dict) -> bool:
    vol = row.get("volume")
    if vol is not None and float(vol) <= 0:
        return True
    o = float(row.get("open") or 0)
    h = float(row.get("high") or 0)
    l = float(row.get("low") or 0)
    c = float(row.get("close") or 0)
    if o == 0 and h == 0 and l == 0 and c == 0:
        return True
    return False


def limit_state(row: dict, prev_close: float | None) -> str:
    """返回 'up' / 'down' / 'none'。

    up=涨停封死（不可买），down=跌停封死（不可卖）。
    用一字板判定封死：全天 O==H==L==C 且触及涨跌停。
    """
    if prev_close is None or prev_close <= 0:
        return "none"
    o = float(row.get("open") or 0)
    h = float(row.get("high") or 0)
    l = float(row.get("low") or 0)
    c = float(row.get("close") or 0)
    if o <= 0 or h <= 0:
        return "none"
    # 一字板：四价相等
    is_one_price = abs(h - l) < 1e-6 and abs(o - c) < 1e-6
    if not is_one_price:
        return "none"
    if c >= prev_close * 1.097:
        return "up"
    if c <= prev_close * 0.907:
        return "down"
    return "none"


def can_buy(row: dict, prev_close: float | None) -> bool:
    if is_suspended(row):
        return False
    return limit_state(row, prev_close) != "up"


def can_sell(code: str, row: dict, prev_close: float | None, t1_locked: set[str]) -> bool:
    if code in t1_locked:
        return False  # T+1：今买明卖
    if is_suspended(row):
        return False
    return limit_state(row, prev_close) != "down"


def round_lot(shares: int, lot: int = 100) -> int:
    """整手化（A股 100 股一手）。"""
    if shares <= 0:
        return 0
    return (shares // lot) * lot


def shares_for_amount(price: float, amount: float, lot: int = 100) -> int:
    """给定金额反推整手股数。"""
    if price <= 0:
        return 0
    raw = int(amount / price)
    return round_lot(raw, lot)
