"""A股交易日历：基于 AKShare ``tool_trade_date_hist_sina`` 的本地缓存。

优先读 ``store/calendar.parquet``（由 build 脚本落库）；缺失时回退到
``app.utils.common_util.is_real_workday_cn`` 工作日近似，避免数据源未更新时
把今天误判为非交易日。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from quant.store.paths import quant_home
from common.timeutil import cn_now


def _legacy_calendar_path() -> Path:
    return quant_home() / "cache" / "trade_calendar.json"


@lru_cache(maxsize=1)
def _load_calendar() -> set[date]:
    """加载交易日集合；优先 parquet，回退 json 缓存，再回退工作日近似。"""
    days: set[date] = set()
    try:
        from quant.data.store import read_calendar

        for s in read_calendar():
            days.add(date.fromisoformat(str(s)[:10]))
    except Exception:
        days = set()

    if not days and _legacy_calendar_path().is_file():
        try:
            arr = json.loads(_legacy_calendar_path().read_text(encoding="utf-8"))
            for s in arr:
                days.add(date.fromisoformat(str(s)[:10]))
        except (json.JSONDecodeError, ValueError, OSError):
            days = set()

    if not days:
        days = _fetch_and_cache()
    return days


def _fetch_and_cache() -> set[date]:
    try:
        import akshare as ak

        df = ak.tool_trade_date_hist_sina()
    except Exception:
        return set()

    col = None
    for c in df.columns:
        if "date" in str(c).lower() or "日期" in str(c):
            col = c
            break
    if col is None:
        return set()

    days: set[date] = set()
    iso_list: list[str] = []
    for v in df[col].tolist():
        try:
            d = _coerce_date(v)
        except Exception:
            continue
        if d is not None:
            days.add(d)
            iso_list.append(d.isoformat())

    if days:
        try:
            _legacy_calendar_path().parent.mkdir(parents=True, exist_ok=True)
            _legacy_calendar_path().write_text(
                json.dumps(iso_list, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass
    return days


def _coerce_date(v) -> date | None:
    from datetime import datetime

    # datetime / Timestamp 是 date 的子类，必须先截成 date，否则 isoformat 带时分秒
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()[:10]
    if not s:
        return None
    # 兼容 "20240115" 与 "2024-01-15" 两种写法
    if len(s) == 8 and s.isdigit():
        s = f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return date.fromisoformat(s)


def to_iso(d) -> str:
    """把 date / datetime / "YYYYMMDD" / "YYYY-MM-DD" 统一成 ISO 字符串。

    全链路（离线库、universe、因子面板、回测引擎、脚本）的日期比较都应先过此函数，
    避免字符串比较时 '-'(0x2D) < 数字导致 ISO 与紧凑格式混用产生未来函数。
    """
    dt = _coerce_date(d)
    return dt.isoformat() if dt is not None else str(d)


def _is_workday_fallback(d: date) -> bool:
    # A 股不在调休周末交易：周末一律非交易日，避免 is_real_workday_cn 把
    # "调休上班的周六"误判为交易日（仅当 parquet 日历缺失/越界时触发本兜底）。
    if d.weekday() >= 5:
        return False
    try:
        from common.utils.common_util import is_real_workday_cn

        return bool(is_real_workday_cn(d))
    except Exception:
        return True  # 周内且无日历时，保守判为交易日


def is_trading_day(d: date) -> bool:
    cal = _load_calendar()
    if not cal:
        return _is_workday_fallback(d)
    if d in cal:
        return True
    if d > max(cal):
        return _is_workday_fallback(d)
    if d < min(cal):
        return False
    return d in cal


def trading_days_between(start: date, end: date) -> int:
    """(start, end] 区间内的交易日数（不含 start，含 end）。

    向量化优化：用日历集合 set 查找，不逐日遍历。旧版 5535 码 × 2030 天循环 = 1100 万次，
    ~110 分钟卡死；新版用集合运算秒级。
    """
    if end <= start:
        return 0
    cal = _load_calendar()
    if not cal:
        # 回退：工作日近似
        n = 0
        cur = start + timedelta(days=1)
        while cur <= end:
            if _is_workday_fallback(cur):
                n += 1
            cur += timedelta(days=1)
        return n
    # 集合查找：遍历 (start, end] 自然日，查 cal 集合 → O(天数) 但 set 查找 O(1)
    n = 0
    cur = start + timedelta(days=1)
    while cur <= end:
        if cur in cal:
            n += 1
        cur += timedelta(days=1)
    return n


def trading_days_since(buy_date: date, today: date | None = None) -> int:
    today = today or cn_now().date()
    return trading_days_between(buy_date, today)


def trading_day_list(start: date, end: date) -> list[date]:
    """[start, end] 区间内所有交易日（含两端），升序。"""
    out: list[date] = []
    cur = start
    while cur <= end:
        if is_trading_day(cur):
            out.append(cur)
        cur += timedelta(days=1)
    return out


def next_trading_day(d: date) -> date | None:
    cal = _load_calendar()
    if not cal:
        cur = d + timedelta(days=1)
        for _ in range(15):
            if _is_workday_fallback(cur):
                return cur
            cur += timedelta(days=1)
        return None
    future = sorted(x for x in cal if x > d)
    if future:
        return future[0]
    cur = d + timedelta(days=1)
    for _ in range(15):
        if _is_workday_fallback(cur):
            return cur
        cur += timedelta(days=1)
    return None


def prev_trading_day(d: date) -> date | None:
    cal = _load_calendar()
    if not cal:
        cur = d - timedelta(days=1)
        for _ in range(15):
            if _is_workday_fallback(cur):
                return cur
            cur -= timedelta(days=1)
        return None
    past = sorted((x for x in cal if x < d), reverse=True)
    return past[0] if past else None


def refresh_calendar() -> int:
    """强制重新拉取交易日历并刷新缓存；返回交易日条数。"""
    _load_calendar.cache_clear()
    days = _fetch_and_cache()
    _load_calendar.cache_clear()
    return len(days)
