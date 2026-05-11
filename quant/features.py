"""Feature extraction helpers for GoldQuant payloads."""

from __future__ import annotations

from typing import Any


def unwrap_payload(raw: dict[str, Any]) -> dict[str, Any]:
    data = raw.get("data") if isinstance(raw, dict) else None
    return data if isinstance(data, dict) else raw


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = value.strip().replace("%", "").replace(",", "")
        if value in {"-", "--"}:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> int | None:
    number = to_float(value)
    return int(number) if number is not None else None


def stock_code(row: dict[str, Any]) -> str:
    for key in ("股票代码", "代码", "symbol", "code"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def stock_name(row: dict[str, Any]) -> str:
    for key in ("股票名称", "名称", "name"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def latest_price(row: dict[str, Any]) -> float | None:
    for key in ("最新价", "现价", "收盘", "收盘价", "close"):
        value = to_float(row.get(key))
        if value is not None and value > 0:
            return value
    pk = row.get("盘口")
    if isinstance(pk, dict):
        for key in ("最新价", "当前价", "买1", "卖1"):
            value = to_float(pk.get(key))
            if value is not None and value > 0:
                return value
    bars = history_bars(row)
    if bars:
        return to_float(bars[-1].get("收盘") or bars[-1].get("收盘价") or bars[-1].get("close"))
    return None


def float_market_cap_yi(row: dict[str, Any]) -> float | None:
    value = to_float(row.get("流通市值"))
    if value is None:
        return None
    return value / 100_000_000 if value > 10_000 else value


def popularity_rank(row: dict[str, Any]) -> int | None:
    return to_int(row.get("人气排名") or row.get("排名"))


def history_bars(row: dict[str, Any]) -> list[dict[str, Any]]:
    bars = row.get("历史行情") or row.get("盘中10分钟线") or []
    return [x for x in bars if isinstance(x, dict)] if isinstance(bars, list) else []


def latest_close(row: dict[str, Any]) -> float | None:
    bars = history_bars(row)
    if not bars:
        return latest_price(row)
    return to_float(bars[-1].get("收盘") or bars[-1].get("收盘价") or bars[-1].get("close"))


def max_recent_high(row: dict[str, Any], lookback: int = 30) -> float | None:
    highs: list[float] = []
    for bar in history_bars(row)[-lookback:]:
        value = to_float(bar.get("最高") or bar.get("最高价") or bar.get("high"))
        if value is not None:
            highs.append(value)
    return max(highs) if highs else None


def max_pullback_pct(row: dict[str, Any], lookback: int = 30) -> float | None:
    high = max_recent_high(row, lookback=lookback)
    close = latest_close(row)
    if high is None or close is None or high <= 0:
        return None
    return (high - close) / high * 100


def has_recent_limit_up(row: dict[str, Any], lookback: int = 30) -> bool:
    for bar in history_bars(row)[-lookback:]:
        pct = to_float(bar.get("涨跌幅") or bar.get("pct_chg"))
        if pct is not None and pct >= 9.8:
            return True
    return False


def average_volume(row: dict[str, Any], lookback: int = 5) -> float | None:
    vols: list[float] = []
    for bar in history_bars(row)[-lookback:]:
        value = to_float(bar.get("成交量") or bar.get("volume"))
        if value is not None:
            vols.append(value)
    return sum(vols) / len(vols) if vols else None


def volume_ratio(row: dict[str, Any]) -> float | None:
    explicit = to_float(row.get("量比"))
    if explicit is not None:
        return explicit
    bars = history_bars(row)
    if len(bars) < 2:
        return None
    latest = to_float(bars[-1].get("成交量") or bars[-1].get("volume"))
    avg = average_volume({"历史行情": bars[:-1]}, lookback=5)
    if latest is None or avg is None or avg <= 0:
        return None
    return latest / avg


def index_by_code(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {stock_code(row): row for row in rows if stock_code(row)}
