"""将盘前/盘中/盘后聚合结果落盘，并基于累积日线合并计算均线、ATR、MACD 等。

目录结构（默认 ``~/data/quant/archive``，可用 ``GOLDQUANT_QUANT_ARCHIVE_DIR`` 覆盖）::

    snapshots/YYYYMMDD/HHMMSS_<phase>.json   # 每次请求的完整 JSON
    bars/<股票代码>.jsonl                    # 按日合并的 OHLCV（一行一日）
    computed/<股票代码>.json                 # 基于 bars 重算的最新指标

交易日调用越频繁、跨日越多，``bars`` 越长，``computed`` 中越接近策略所需精度。

**日线拉取策略（与自选/持仓无关，按股票代码维度）：**

- 某代码在 ``bars/<代码>.jsonl`` 尚不存在或为空：从 ``QUANT_HIST_FULL_START_DATE`` 起 **全量** 拉至今日并合并落盘。
- 已有归档且**最后一根早于今天**：``start_date`` 取「最后一根日期的**自然日次日**」至今日；东财只返回交易日，**中间若多日未跑也会一次补全**，避免「只拉近 N 天」造成永久缺口。
- 已有归档且**最后一根已是今天**（同日多次请求）：向前重叠拉 ``QUANT_HIST_INCREMENTAL_TRADE_DAYS`` 个交易日，用于复权修正与当日刷新。
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from app.core.config import Settings
from app.utils.common_util import get_n_workdays_ago, get_val, today

logger = logging.getLogger(__name__)

Phase = Literal["pre", "during", "post"]

_STOCK_LIST_KEYS = ("自选股", "持仓股", "同花顺人气股")


def quant_archive_base(settings: Settings) -> Path:
    raw = (settings.QUANT_ARCHIVE_DIR or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.home() / "data" / "quant" / "archive"


def normalized_full_start_date(settings: Settings) -> str:
    """日线全量拉取起始 ``YYYYMMDD``。"""
    s = (settings.QUANT_HIST_FULL_START_DATE or "20050101").strip().replace("-", "")[:8]
    return s if len(s) == 8 and s.isdigit() else "20050101"


def _norm_date_key(raw: Any) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().replace("-", "").replace("/", "")[:8]
    if len(s) >= 8 and s[:8].isdigit():
        return s[:8]
    m = re.search(r"(\d{4})(\d{2})(\d{2})", s)
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}"
    return None


def _f(x: Any) -> float | None:
    try:
        if x is None or x == "":
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _bar_from_hist_row(row: dict[str, Any]) -> dict[str, Any] | None:
    dk = _norm_date_key(get_val(row, "日期"))
    if not dk:
        return None
    o, c, h, l = _f(get_val(row, "开盘")), _f(get_val(row, "收盘")), _f(get_val(row, "最高")), _f(get_val(row, "最低"))
    if c is None:
        return None
    return {
        "date": dk,
        "open": o if o is not None else c,
        "high": h if h is not None else c,
        "low": l if l is not None else c,
        "close": c,
        "volume": _f(get_val(row, "成交量", 0)) or 0.0,
        "amount": _f(get_val(row, "成交额", 0)) or 0.0,
    }


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(text)
        Path(tmp).replace(path)
    except Exception:
        try:
            Path(tmp).unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _read_bars_by_date(path: Path) -> dict[str, dict[str, Any]]:
    by_date: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return by_date
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            d = o.get("date")
            if isinstance(d, str) and len(d) == 8:
                by_date[d] = o
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("读取 bars 失败 path=%s: %s", path, e)
    return by_date


def _write_bars_by_date(path: Path, by_date: dict[str, dict[str, Any]]) -> None:
    if not by_date:
        return
    lines = [json.dumps(by_date[k], ensure_ascii=False) for k in sorted(by_date)]
    _atomic_write_text(path, "\n".join(lines) + "\n")


def _bar_to_hist_row(bar: dict[str, Any], symbol: str) -> dict[str, Any]:
    d = bar["date"]
    ds = f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else str(d)
    return {
        "日期": ds,
        "股票代码": symbol,
        "开盘": bar["open"],
        "收盘": bar["close"],
        "最高": bar["high"],
        "最低": bar["low"],
        "成交量": bar["volume"],
        "成交额": bar["amount"],
    }


def symbol_needs_full_daily_fetch(settings: Settings, symbol: str) -> bool:
    """本地尚无该股日线合并文件时，需要全量拉取。"""
    path = quant_archive_base(settings) / "bars" / f"{str(symbol).strip()}.jsonl"
    if not path.is_file() or path.stat().st_size == 0:
        return True
    return False


def last_daily_bar_date(settings: Settings, symbol: str) -> str | None:
    """本地 bars 中最后一根日线的 ``YYYYMMDD``；无文件或为空返回 ``None``。"""
    path = quant_archive_base(settings) / "bars" / f"{str(symbol).strip()}.jsonl"
    by_date = _read_bars_by_date(path)
    if not by_date:
        return None
    return max(by_date.keys())


def _calendar_day_after_yyyymmdd(d8: str) -> str:
    dt = datetime.strptime(d8, "%Y%m%d").date()
    return (dt + timedelta(days=1)).strftime("%Y%m%d")


def daily_hist_fetch_start_date(settings: Settings, symbol: str) -> str:
    """日线 ``hist`` 的 ``start_date``：全量 / 补缺 / 当日重叠刷新。"""
    if symbol_needs_full_daily_fetch(settings, symbol):
        return normalized_full_start_date(settings)
    last = last_daily_bar_date(settings, symbol)
    if not last:
        return normalized_full_start_date(settings)
    today_s = today()
    full_start = normalized_full_start_date(settings)
    if last < today_s:
        ns = _calendar_day_after_yyyymmdd(last)
        if ns > today_s:
            ns = today_s
        return max(ns, full_start)
    n_ov = max(1, int(settings.QUANT_HIST_INCREMENTAL_TRADE_DAYS))
    start = get_n_workdays_ago(n=n_ov)
    if start is None:
        start = get_n_workdays_ago(n=9)
    return start


def load_merge_write_daily_bars(
    settings: Settings,
    symbol: str,
    api_rows: list[Any] | None,
) -> list[dict[str, Any]]:
    """将接口返回的日线与本地 ``bars`` 合并写盘，并返回与东财接口风格一致的完整 ``历史行情`` 列表（按日期升序）。"""
    symbol = str(symbol).strip()
    base = quant_archive_base(settings)
    bars_dir = base / "bars"
    bars_dir.mkdir(parents=True, exist_ok=True)
    path = bars_dir / f"{symbol}.jsonl"
    by_date = _read_bars_by_date(path)
    for row in api_rows or []:
        if not isinstance(row, dict):
            continue
        bar = _bar_from_hist_row(row)
        if bar:
            by_date[bar["date"]] = bar
    if by_date:
        _write_bars_by_date(path, by_date)
        out = [_bar_to_hist_row(by_date[k], symbol) for k in sorted(by_date)]
    else:
        out = []
    if by_date:
        metrics = recompute_symbol_metrics(path)
        if metrics:
            comp_dir = base / "computed"
            comp_dir.mkdir(parents=True, exist_ok=True)
            comp_path = comp_dir / f"{symbol}.json"
            try:
                _atomic_write_text(comp_path, json.dumps(metrics, ensure_ascii=False, indent=2) + "\n")
            except OSError as e:
                logger.warning("写入 computed 失败 %s: %s", comp_path, e)
    return out


def _merge_hist_into_symbol(bars_dir: Path, code: str, hist: list[Any]) -> None:
    code = str(code).strip()
    if not code or not hist:
        return
    path = bars_dir / f"{code}.jsonl"
    by_date = _read_bars_by_date(path)
    for row in hist:
        if not isinstance(row, dict):
            continue
        bar = _bar_from_hist_row(row)
        if bar:
            by_date[bar["date"]] = bar
    if not by_date:
        return
    _write_bars_by_date(path, by_date)


def _merge_all_bars_from_payload(base: Path, payload: dict[str, Any]) -> None:
    bars_dir = base / "bars"
    for key in _STOCK_LIST_KEYS:
        rows = payload.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = row.get("股票代码")
            hist = row.get("历史行情")
            if code and isinstance(hist, list):
                _merge_hist_into_symbol(bars_dir, str(code), hist)


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


def recompute_symbol_metrics(bars_path: Path) -> dict[str, Any] | None:
    if not bars_path.is_file():
        return None
    rows: list[dict[str, Any]] = []
    try:
        for line in bars_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("重算指标时读取失败 %s: %s", bars_path, e)
        return None
    rows.sort(key=lambda x: x.get("date", ""))
    if not rows:
        return None
    closes = [float(r["close"]) for r in rows]
    highs = [float(r["high"]) for r in rows]
    lows = [float(r["low"]) for r in rows]
    tr = _compute_tr(highs, lows, closes)
    _, atr14 = _atr_wilder(tr, 14)
    macd = _macd_last(closes)
    last = rows[-1]
    sym = bars_path.stem
    out: dict[str, Any] = {
        "股票代码": sym,
        "bars_count": len(rows),
        "first_date": rows[0].get("date"),
        "latest_date": last.get("date"),
        "latest_close": last.get("close"),
        "MA5": _ma_last(closes, 5),
        "MA10": _ma_last(closes, 10),
        "MA20": _ma_last(closes, 20),
        "ATR14": round(atr14, 6) if atr14 is not None else None,
        "MACD": macd,
        "computed_at": datetime.now().isoformat(timespec="seconds"),
    }
    return out


def load_computed_metrics_zh(settings: Settings, symbol: str) -> dict[str, Any] | None:
    """读取本地 ``computed`` 指标，转为全中文键，便于模型消费。"""
    path = quant_archive_base(settings) / "computed" / f"{str(symbol).strip()}.json"
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("读取技术指标失败 symbol=%s: %s", symbol, e)
        return None
    macd = raw.get("MACD") if isinstance(raw.get("MACD"), dict) else {}
    return {
        "本地K线根数": raw.get("bars_count"),
        "指标截至日期": raw.get("latest_date"),
        "最新收盘价": raw.get("latest_close"),
        "均线5日": raw.get("MA5"),
        "均线10日": raw.get("MA10"),
        "均线20日": raw.get("MA20"),
        "ATR14": raw.get("ATR14"),
        "MACD": {
            "差离值": macd.get("dif"),
            "信号线": macd.get("dea"),
            "柱": macd.get("histogram"),
        },
        "指标计算时间": raw.get("computed_at"),
    }


def recompute_all_computed(base: Path) -> None:
    bars_dir = base / "bars"
    comp_dir = base / "computed"
    if not bars_dir.is_dir():
        return
    for p in sorted(bars_dir.glob("*.jsonl")):
        metrics = recompute_symbol_metrics(p)
        if not metrics:
            continue
        comp_path = comp_dir / f"{p.stem}.json"
        try:
            _atomic_write_text(comp_path, json.dumps(metrics, ensure_ascii=False, indent=2) + "\n")
        except OSError as e:
            logger.warning("写入 computed 失败 %s: %s", comp_path, e)


def archive_market_sync(phase: Phase, payload: dict[str, Any], settings: Settings) -> None:
    """供 BackgroundTasks 调用的同步入口：写快照、合并 K 线、重算指标。"""
    if not settings.QUANT_ARCHIVE_ENABLED:
        return
    try:
        base = quant_archive_base(settings)
        now = datetime.now()
        day_s = now.strftime("%Y%m%d")
        time_s = now.strftime("%H%M%S")
        snap_dir = base / "snapshots" / day_s
        snap_dir.mkdir(parents=True, exist_ok=True)
        snap_path = snap_dir / f"{time_s}_{phase}.json"
        body = {
            "phase": phase,
            "saved_at": now.isoformat(timespec="seconds"),
            "data": payload,
        }
        _atomic_write_text(snap_path, json.dumps(body, ensure_ascii=False, indent=2) + "\n")
        _merge_all_bars_from_payload(base, payload)
        recompute_all_computed(base)
    except Exception:
        logger.exception("量化归档失败 phase=%s", phase)
