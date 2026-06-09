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


def hist_rows_sorted(hist: object) -> list[dict]:
    """历史行情按日期升序的有效 K 线行。"""
    if not isinstance(hist, list):
        return []
    rows = [r for r in hist if isinstance(r, dict) and hist_close(r) is not None]
    rows.sort(key=lambda r: str(r.get("日期") or r.get("date") or ""))
    return rows


def hist_daily_changes(hist: object) -> list[float]:
    """相邻交易日收盘涨跌幅(%)，有行内涨跌幅则用之，否则由收盘价推算。"""
    rows = hist_rows_sorted(hist)
    changes: list[float] = []
    prev_close: float | None = None
    for row in rows:
        close = hist_close(row)
        if close is None:
            continue
        if prev_close is not None and prev_close > 0:
            explicit = metric_from_dict(row, "涨跌幅", "pct_chg", "涨跌")
            if explicit is not None:
                changes.append(explicit)
            else:
                changes.append((close - prev_close) / prev_close * 100)
        prev_close = close
    return changes


def hist_closes(hist: object) -> list[float]:
    return [c for c in (hist_close(r) for r in hist_rows_sorted(hist)) if c is not None]


def _hist_row_date(row: dict) -> str:
    return str(row.get("日期") or row.get("date") or "").strip()[:10]


def _period_closes(hist: object, *, period: str) -> list[float]:
    """由日线聚合周/月收盘价序列（每周期取最后一根收盘）。"""
    from datetime import datetime

    rows = hist_rows_sorted(hist)
    if not rows:
        return []
    buckets: dict[str, float] = {}
    for row in rows:
        ds = _hist_row_date(row)
        close = hist_close(row)
        if not ds or close is None:
            continue
        try:
            dt = datetime.strptime(ds.replace("/", "-"), "%Y-%m-%d")
        except ValueError:
            continue
        if period == "weekly":
            key = f"{dt.isocalendar().year}-W{dt.isocalendar().week:02d}"
        else:
            key = f"{dt.year}-{dt.month:02d}"
        buckets[key] = close
    return [buckets[k] for k in sorted(buckets)]


def period_trend_up(closes: list[float]) -> bool:
    """周期收盘序列末端向上（最新 > 前一周期，且不低于前三周期）。"""
    if len(closes) < 2:
        return False
    if closes[-1] <= closes[-2]:
        return False
    if len(closes) >= 3:
        return closes[-1] >= closes[-3]
    return True


def ma_spread_pct(closes: list[float]) -> float | None:
    """日线 MA5 相对 MA20 的发散幅度(%)。"""
    if len(closes) < 20:
        return None
    ma5 = sum(closes[-5:]) / 5
    ma20 = sum(closes[-20:]) / 20
    if ma20 <= 0:
        return None
    return (ma5 - ma20) / ma20 * 100


def ma_bull_from_closes(closes: list[float]) -> bool:
    if len(closes) < 20:
        return False
    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10
    ma20 = sum(closes[-20:]) / 20
    return ma5 > ma10 > ma20


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


def monthly_return_pct(stock: dict, *, days: int = 22) -> float | None:
    """近 N 个交易日涨幅(%)，默认约一月。"""
    closes = hist_closes(stock.get("历史行情") or [])
    if len(closes) < days + 1:
        return None
    start = closes[-days - 1]
    end = closes[-1]
    if start <= 0:
        return None
    return (end - start) / start * 100


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
