"""可交易性判定：停牌 + 涨跌停分档 + 封板 + T+1。

A股涨跌停分档：
- 主板/沪深 10%
- ST 5%
- 创业板(300)/科创板(688) 20%
- 北交(8/4) 30%

封板判定（不再要求一字板）：
- 涨停封死：close >= prev_close*(1+limit) 且 high==close（全天未能跌破涨停价）→ 买不进
- 跌停封死：close <= prev_close*(1-limit) 且 low==close → 卖不出
- 一字板是封死的特例，自动覆盖
"""

from __future__ import annotations

import numpy as np


def _limit_pct(code: str, name: str | None = None) -> float:
    """返回涨跌停比例（0.10/0.05/0.20/0.30）。"""
    c = str(code).strip()
    # 北交 8/4 开头 30%
    if c.startswith(("8", "4")):
        return 0.30
    # 创业板 300 / 科创板 688 20%
    if c.startswith(("300", "688")):
        return 0.20
    # ST 5%
    if name and any(m in str(name).upper() for m in ("ST", "*ST", "退")):
        return 0.05
    # 主板 10%
    return 0.10


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


def limit_state(row: dict, prev_close: float | None, *, code: str | None = None, name: str | None = None) -> str:
    """返回 'up' / 'down' / 'none'。

    up=涨停封死（不可买），down=跌停封死（不可卖）。
    封板判定：收盘价等于涨停价（且 high==close）视为封死买不进；
    收盘价等于跌停价（且 low==close）视为封死卖不出。
    """
    if prev_close is None or prev_close <= 0:
        return "none"
    o = float(row.get("open") or 0)
    h = float(row.get("high") or 0)
    l = float(row.get("low") or 0)
    c = float(row.get("close") or 0)
    if o <= 0 or h <= 0:
        return "none"
    limit = _limit_pct(code or str(row.get("code", "")), name or row.get("name"))
    up_price = prev_close * (1 + limit)
    down_price = prev_close * (1 - limit)
    # 涨停封死：收盘在涨停价附近且 high==close（全天未跌破涨停价）
    if abs(c - up_price) < 1e-3 and abs(h - c) < 1e-3:
        return "up"
    # 跌停封死：收盘在跌停价附近且 low==close
    if abs(c - down_price) < 1e-3 and abs(l - c) < 1e-3:
        return "down"
    return "none"


def can_buy(row: dict, prev_close: float | None, *, code: str | None = None, name: str | None = None) -> bool:
    if is_suspended(row):
        return False
    return limit_state(row, prev_close, code=code, name=name) != "up"


def can_sell(code: str, row: dict, prev_close: float | None, t1_locked: set[str], *, name: str | None = None) -> bool:
    if code in t1_locked:
        return False  # T+1：今买明卖
    if is_suspended(row):
        return False
    return limit_state(row, prev_close, code=code, name=name) != "down"


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
