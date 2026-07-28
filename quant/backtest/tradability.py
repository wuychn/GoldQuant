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

import math

import numpy as np


def _fnum(v) -> float:
    """转 float;None/异常/非有限(NaN/inf)→ nan。

    供停牌/涨跌停判定做保守处理:数据缺失(NaN)不应被当作"可交易"。
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return float("nan")
    return f if math.isfinite(f) else float("nan")


def _limit_pct(code: str, name: str | None = None) -> float:
    """返回涨跌停比例（0.10/0.05/0.20/0.30）。

    判定顺序：北交 → 创业板/科创板 → ST → 主板。创业板/科创板的 ST 股仍为 20%，
    故板块判定必须先于 ST 判定。

    数据依赖：主板 ST 的 5% 判定依赖 ``name`` 列。若 daily 无 name（或未传入），
    ST 股会退化为主板 10%，导致其 5% 涨停封板判不出、回测中产生实际无法成交的
    买单。离线库应保证 name 随日快照落库（天然 PIT）。
    """
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
    vol = _fnum(row.get("volume"))
    if math.isnan(vol) or vol <= 0:
        return True  # 无成交/停牌/数据缺失(NaN)→ 保守判停牌
    o, h, l, c = (_fnum(row.get(k)) for k in ("open", "high", "low", "close"))
    if all(math.isnan(x) or x == 0 for x in (o, h, l, c)):
        return True  # OHLC 全 0(停牌补零)或全 NaN → 停牌
    return False


_PRICE_TOL = 0.005  # 半分：A股报价最小变动单位 0.01 元


def limit_state(row: dict, prev_close: float | None, *, code: str | None = None, name: str | None = None) -> str:
    """返回 'up' / 'down' / 'none'。

    up=涨停封死（不可买），down=跌停封死（不可卖）。
    封板判定：收盘触及涨停价且 high==close（全天未跌破涨停）视为封死买不进；
    收盘触及跌停价且 low==close 视为封死卖不出。

    涨跌停价须先四舍五入到分（交易所口径）：如 prev_close=10.05、limit=10% 时
    理论价 11.055、实际涨停价 11.06，不 round 会因差 0.005 而漏判。
    """
    if prev_close is None or prev_close <= 0:
        return "none"
    o = _fnum(row.get("open"))
    h = _fnum(row.get("high"))
    l = _fnum(row.get("low"))
    c = _fnum(row.get("close"))
    if math.isnan(o) or math.isnan(h) or o <= 0 or h <= 0:
        return "none"
    limit = _limit_pct(code or str(row.get("code", "")), name or row.get("name"))
    up_price = round(prev_close * (1 + limit), 2)
    down_price = round(prev_close * (1 - limit), 2)
    # 涨停封死：收盘已达涨停价且最高价未高于收盘（全天封在涨停）
    if c >= up_price - _PRICE_TOL and h <= c + _PRICE_TOL:
        return "up"
    # 跌停封死：收盘已达跌停价且最低价未低于收盘
    if c <= down_price + _PRICE_TOL and l >= c - _PRICE_TOL:
        return "down"
    return "none"


def price_at_limit(
    price: float,
    prev_close: float | None,
    *,
    code: str | None = None,
    name: str | None = None,
) -> str:
    """给定一个具体价格（开盘/最新/成交价），判定是否触及涨跌停价。

    供 strict 模式（T 开盘成交）使用：开盘价已达涨停价→不可买，已达跌停价→不可卖。
    与 ``limit_state``（基于日 K close==high 判全天封板）互补——后者判"尾盘封板"，
    本函数判"给定时刻价格是否封板"，避免 strict 下用收盘封板口径误判开盘可买。
    """
    if prev_close is None or prev_close <= 0:
        return "none"
    p = _fnum(price)
    if math.isnan(p) or p <= 0:
        return "none"
    limit = _limit_pct(code or "", name)
    up_price = round(prev_close * (1 + limit), 2)
    down_price = round(prev_close * (1 - limit), 2)
    if p >= up_price - _PRICE_TOL:
        return "up"
    if p <= down_price + _PRICE_TOL:
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


def cap_shares_by_adv(
    shares: int,
    *,
    price: float,
    day_amount: float | None,
    max_pct: float = 0.05,
    lot: int = 100,
) -> int:
    """单票单日成交额不超过当日总成交额的 max_pct（默认 5%）。"""
    if shares <= 0 or price <= 0 or not day_amount or day_amount <= 0 or max_pct <= 0:
        return shares
    max_notional = float(day_amount) * max_pct
    max_shares = round_lot(int(max_notional / price), lot)
    return min(shares, max_shares)
