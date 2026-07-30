"""个股技术指标计算（MA / ATR / MACD）——纯函数。

从日K bar 序列算指标，供 ``services/enrich.py`` 读离线库后内联计算 ``技术指标``。
``computed_raw_to_zh`` 把指标 dict 转中英双键（LLM 中文 + scoring 英文共用）。

历史背景：本模块原为 ``archive.py``（写 ``~/.quant/archive`` 的 bars/computed jsonl
+ 每请求快照归档）。enrich 改读 ``store/daily_raw`` 离线库 + 内联算指标后，归档
子系统全部退役（快照特性 ``archive_market_sync`` 从未接上线），仅保留指标纯函数，
并更名为 ``indicators``。
"""

from __future__ import annotations

from typing import Any

from common.config import Settings


def normalized_full_start_date(settings: Settings) -> str:
    """日线全量拉取起始 ``YYYYMMDD``（兜底 ``20050101``）。"""
    s = (settings.QUANT_HIST_FULL_START_DATE or "20050101").strip().replace("-", "")[:8]
    return s if len(s) == 8 and s.isdigit() else "20050101"


def _ema_series(closes: list[float], span: int) -> list[float]:
    if not closes or span <= 0:
        return []
    k = 2.0 / (span + 1)
    out: list[float] = []
    ema = closes[0]
    out.append(ema)
    for c in closes[1:]:
        ema = c * k + ema * (1 - k)
        out.append(ema)
    return out


def _compute_tr(highs: list[float], lows: list[float], closes: list[float]) -> list[float]:
    tr: list[float] = []
    for i in range(len(closes)):
        h, l, c = highs[i], lows[i], closes[i]
        if i == 0:
            tr.append(h - l)
        else:
            pc = closes[i - 1]
            tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    return tr


def _atr_wilder(tr: list[float], period: int = 14) -> tuple[list[float], float | None]:
    if not tr or period <= 0:
        return [], None
    atr: list[float] = []
    if len(tr) < period:
        return [], None
    first = sum(tr[:period]) / period
    atr.append(first)
    for i in range(period, len(tr)):
        prev = atr[-1]
        atr.append((prev * (period - 1) + tr[i]) / period)
    return atr, atr[-1] if atr else None


def _macd_last(closes: list[float]) -> dict[str, float | None]:
    if len(closes) < 2:
        return {"dif": None, "dea": None, "histogram": None}
    ema12 = _ema_series(closes, 12)
    ema26 = _ema_series(closes, 26)
    dif = [a - b for a, b in zip(ema12, ema26)]
    dea_series = _ema_series(dif, 9)
    hist = [d - e for d, e in zip(dif, dea_series)]
    return {
        "dif": round(dif[-1], 6) if dif else None,
        "dea": round(dea_series[-1], 6) if dea_series else None,
        "histogram": round(hist[-1], 6) if hist else None,
    }


def _ma_last(closes: list[float], n: int) -> float | None:
    if len(closes) < n:
        return None
    return round(sum(closes[-n:]) / n, 4)


def compute_metrics_from_bars(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """从 bar 列表（升序，键含 date/open/high/low/close）算 MA/ATR/MACD。

    纯函数、不读盘：供 ``services/enrich.py`` 读离线库后内联算 ``技术指标``。
    """
    if not rows:
        return None
    closes = [float(r["close"]) for r in rows]
    highs = [float(r["high"]) for r in rows]
    lows = [float(r["low"]) for r in rows]
    tr = _compute_tr(highs, lows, closes)
    _, atr14 = _atr_wilder(tr, 14)
    macd = _macd_last(closes)
    last = rows[-1]
    return {
        "bars_count": len(rows),
        "first_date": rows[0].get("date"),
        "latest_date": last.get("date"),
        "latest_close": last.get("close"),
        "MA5": _ma_last(closes, 5),
        "MA10": _ma_last(closes, 10),
        "MA20": _ma_last(closes, 20),
        "MA30": _ma_last(closes, 30),
        "ATR14": round(atr14, 6) if atr14 is not None else None,
        "MACD": macd,
    }


def computed_raw_to_zh(raw: dict[str, Any]) -> dict[str, Any]:
    """computed 指标 dict → 全中文键（供 LLM）+ 英文键（供 scoring 消费端）。"""
    macd = raw.get("MACD") if isinstance(raw.get("MACD"), dict) else {}
    ma5, ma10, ma20, ma30 = raw.get("MA5"), raw.get("MA10"), raw.get("MA20"), raw.get("MA30")
    latest = raw.get("latest_close")
    atr14 = raw.get("ATR14")
    macd_zh = {
        "差离值": macd.get("dif"),
        "信号线": macd.get("dea"),
        "柱": macd.get("histogram"),
    }
    return {
        "最新收盘价": latest,
        "latest_close": latest,
        "均线5日": ma5,
        "MA5": ma5,
        "均线10日": ma10,
        "MA10": ma10,
        "均线20日": ma20,
        "MA20": ma20,
        "均线30日": ma30,
        "MA30": ma30,
        "ATR14": atr14,
        "atr14": atr14,
        "MACD": macd_zh,
        "指标计算时间": raw.get("computed_at"),
    }
