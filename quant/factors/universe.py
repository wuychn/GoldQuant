"""行业 / 市值字段提取。"""

from __future__ import annotations

import math

from quant.portfolio.constraints import industry_of_stock


def stock_industry(stock: dict, payload: dict | None = None) -> str:
    return industry_of_stock(stock, payload or {})


def float_market_cap_yuan(stock: dict) -> float | None:
    """流通/总市值（元）。"""
    from quant.market.fund_flow import amount_to_yuan

    for key in ("流通市值", "总市值"):
        v = stock.get(key)
        if isinstance(v, (int, float)) and float(v) > 0:
            return float(v)
        yuan = amount_to_yuan(v) if v is not None else None
        if yuan and yuan > 0:
            return yuan
    pk = stock.get("盘口") if isinstance(stock.get("盘口"), dict) else {}
    for key in ("流通市值", "总市值"):
        v = pk.get(key)
        if isinstance(v, (int, float)) and float(v) > 0:
            return float(v)
        yuan = amount_to_yuan(v) if v is not None else None
        if yuan and yuan > 0:
            return yuan
    return None


def log_mcap(stock: dict) -> float | None:
    mv = float_market_cap_yuan(stock)
    if mv is None or mv <= 0:
        return None
    return math.log(mv)
