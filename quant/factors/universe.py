"""行业 / 市值字段提取。"""

from __future__ import annotations

import math

def industry_of_stock(stock: dict, payload: dict) -> str:
    """取个股行业。"""
    for key in ("行业", "所属行业", "板块"):
        val = stock.get(key)
        if isinstance(val, list) and val:
            return str(val[0]).strip()
        if isinstance(val, str) and val.strip():
            return val.split(",")[0].strip()
    code = str(stock.get("股票代码", "")).strip()
    for key in ("自选股", "持仓股"):
        for row in payload.get(key) or []:
            if str(row.get("股票代码", "")).strip() != code:
                continue
            for ik in ("行业", "所属行业", "板块"):
                val = row.get(ik)
                if isinstance(val, list) and val:
                    return str(val[0]).strip()
                if isinstance(val, str) and val.strip():
                    return val.split(",")[0].strip()
    return ""


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
