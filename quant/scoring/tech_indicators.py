"""技术指标 / 盘口字段统一读取（兼容中文键、英文键与归档 computed 格式）。"""

from __future__ import annotations

from typing import Any


def to_float(v: object) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f if f == f else None  # NaN
    try:
        f = float(str(v).strip().replace(",", ""))
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def metric_from_dict(d: dict[str, Any], *keys: str) -> float | None:
    for k in keys:
        if k not in d:
            continue
        f = to_float(d.get(k))
        if f is not None:
            return f
    return None


def macd_scalar(val: object) -> float | None:
    """MACD 可能是标量或 {dif, 差离值, histogram, 柱} 字典。"""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, dict):
        for k in ("dif", "DIF", "差离值", "histogram", "柱", "macd"):
            f = to_float(val.get(k))
            if f is not None:
                return f
    return to_float(val)


def parse_technical_indicators(t: object) -> dict[str, float | None]:
    """从 ``技术指标`` 解析均线、收盘价、MACD、ATR。"""
    empty: dict[str, float | None] = {
        "ma5": None,
        "ma10": None,
        "ma20": None,
        "last_close": None,
        "macd": None,
        "atr14": None,
    }
    if not isinstance(t, dict) or not t:
        return empty
    return {
        "ma5": metric_from_dict(t, "MA5", "均线5日", "ma5"),
        "ma10": metric_from_dict(t, "MA10", "均线10日", "ma10"),
        "ma20": metric_from_dict(t, "MA20", "均线20日", "ma20"),
        "last_close": metric_from_dict(
            t, "latest_close", "最新收盘价", "最新收盘", "last_close"
        ),
        "macd": macd_scalar(t.get("MACD")),
        "atr14": metric_from_dict(t, "ATR14", "atr14"),
    }


def _pk_dict(stock: dict) -> dict[str, Any]:
    pk = stock.get("盘口")
    return pk if isinstance(pk, dict) else {}


def quote_last_price(stock: dict) -> float | None:
    """现价：盘口最新价优先，否则技术指标里的最新收盘。"""
    for k in ("最新", "最新价"):
        f = to_float(_pk_dict(stock).get(k))
        if f is not None and f > 0:
            return f
    return parse_technical_indicators(stock.get("技术指标")).get("last_close")


def quote_open_price(stock: dict, *, fallback: float | None = None) -> float | None:
    for k in ("今开", "开盘", "开盘价", "open"):
        f = to_float(_pk_dict(stock).get(k))
        if f is not None and f > 0:
            return f
    return fallback


def quote_avg_price(stock: dict) -> float | None:
    for k in ("均价", "平均价", "均价线"):
        f = to_float(_pk_dict(stock).get(k))
        if f is not None and f > 0:
            return f
    return None


def quote_change_pct(stock: dict) -> float | None:
    for k in ("涨幅", "涨跌幅", "change_pct"):
        f = to_float(_pk_dict(stock).get(k))
        if f is not None:
            return f
    return None


def hist_close(row: dict) -> float | None:
    return metric_from_dict(row, "收盘", "close", "收盘价")


def hist_change_pct(row: dict) -> float | None:
    f = metric_from_dict(row, "涨跌幅", "pct_chg", "涨跌")
    return 0.0 if f is None else f


def stock_daily_change_pct(stock: dict) -> float | None:
    """个股当日涨跌幅(%)：盘口优先，否则取历史行情最近一条（缺失则为 None）。"""
    chg = quote_change_pct(stock)
    if chg is not None:
        return chg
    hist = stock.get("历史行情") or []
    if isinstance(hist, list) and hist:
        last = hist[-1]
        if isinstance(last, dict):
            return metric_from_dict(last, "涨跌幅", "pct_chg", "涨跌")
    return None


def mas_from_stock(stock: dict) -> dict[str, float | None]:
    """主升浪 / 技术评分共用的均线 + 现价 + MACD 包。"""
    ti = parse_technical_indicators(stock.get("技术指标"))
    last = quote_last_price(stock)
    return {
        "ma5": ti["ma5"],
        "ma10": ti["ma10"],
        "ma20": ti["ma20"],
        "last": last,
        "macd": ti["macd"],
        "atr14": ti["atr14"],
    }
