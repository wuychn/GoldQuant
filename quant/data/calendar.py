"""A股交易日历：基于 AKShare ``tool_trade_date_hist_sina`` 的本地缓存。

提供：
- ``is_trading_day(d)`` / ``trading_days_between(a, b)`` / ``trading_day_list(a, b)``
- ``trading_days_since(buy_date, today)``：用于时间止损的持仓交易日计数

数据源仅返回历史交易日；当日与未来日由 ``is_real_workday_cn`` 兜底判定
（周一至周五且非法定休日），避免在数据源未更新时把今天误判为非交易日。
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from quant.store.paths import quant_home
from quant.timeutil import cn_now

_MTIME_THROTTLE_SEC = 1.0  # 同一秒内不重复 stat，避免回测逐日循环被 FS 拖死


def _calendar_path() -> Path:
    return quant_home() / "cache" / "trade_calendar.json"


_cached_days: set[date] | None = None
_cached_mtime: float | None = None
_last_check_mono: float = 0.0
_sorted_days: list[date] | None = None


def _load_calendar_fresh() -> set[date]:
    """带 mtime 失效 + 时间节流的日历加载。

    - 空集不永久卡死：节流窗口过后会重试拉取
    - 合并为单次 ``os.stat``，避免 ``is_file`` + ``stat`` 双 syscall
    """
    global _cached_days, _cached_mtime, _last_check_mono, _sorted_days

    now = time.monotonic()
    if _cached_days is not None and (now - _last_check_mono) < _MTIME_THROTTLE_SEC:
        return _cached_days
    _last_check_mono = now

    path = _calendar_path()
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        mtime = None

    if _cached_days is not None and mtime == _cached_mtime and _cached_days:
        return _cached_days

    days: set[date] = set()
    if mtime is not None:
        try:
            arr = json.loads(path.read_text(encoding="utf-8"))
            for s in arr:
                d = _coerce_date(s)
                if d is not None:
                    days.add(d)
        except (json.JSONDecodeError, ValueError, OSError):
            days = set()

    if not days:
        days = _fetch_and_cache()
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            mtime = None

    _cached_days = days
    _cached_mtime = mtime
    _sorted_days = sorted(days) if days else []
    return days


def _sorted_calendar() -> list[date]:
    _load_calendar_fresh()
    return _sorted_days or []


def _fetch_and_cache() -> set[date]:
    """从 AKShare 拉取交易日历并落盘；失败返回空集（调用方走兜底）。"""
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
    out: list[str] = []
    for v in df[col].tolist():
        try:
            d = _coerce_date(v)
        except Exception:
            continue
        if d is not None:
            days.add(d)
            out.append(d.isoformat())

    if days:
        try:
            _calendar_path().parent.mkdir(parents=True, exist_ok=True)
            _calendar_path().write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return days


def _coerce_date(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()[:10]
    if not s:
        return None
    if len(s) == 8 and s.isdigit():
        return date(int(s[:4]), int(s[4:6]), int(s[6:]))
    return date.fromisoformat(s)


def _is_workday_fallback(d: date) -> bool:
    """数据源未覆盖时的兜底：周一至周五且非法定休日。"""
    try:
        from app.utils.common_util import is_real_workday_cn

        return bool(is_real_workday_cn(d))
    except Exception:
        return d.weekday() < 5


def is_trading_day(d: date) -> bool:
    """是否为A股交易日。

    命中已缓存日历直接返回；未命中（含当日/未来日）走工作日兜底，
    避免日历未更新到今天时误判为非交易日。
    """
    cal = _load_calendar_fresh()
    if not cal:
        return _is_workday_fallback(d)
    if d in cal:
        return True
    if d > max(cal):
        return _is_workday_fallback(d)
    if d < min(cal):
        return False
    return False


def trading_days_between(start: date, end: date) -> int:
    """(start, end] 区间内的交易日数（不含 start，含 end）。

    单次取日历集合后计数，避免逐日 ``is_trading_day`` 触发重复 stat。
    """
    if end <= start:
        return 0
    cal = _load_calendar_fresh()
    if cal:
        return sum(1 for d in cal if start < d <= end)
    # 空日历：走工作日兜底（慢路径，仅失败时触发）
    n = 0
    cur = start + timedelta(days=1)
    while cur <= end:
        if _is_workday_fallback(cur):
            n += 1
        cur += timedelta(days=1)
    return n


def trading_day_list(start: date, end: date) -> list[date]:
    """[start, end] 区间内所有交易日（含两端），升序。"""
    if end < start:
        return []
    cal = _load_calendar_fresh()
    if cal:
        return sorted(d for d in cal if start <= d <= end)
    out: list[date] = []
    cur = start
    while cur <= end:
        if _is_workday_fallback(cur):
            out.append(cur)
        cur += timedelta(days=1)
    return out


def trading_days_since(buy_date: date, today: date | None = None) -> int:
    """买入日之后到 today（含）的交易日数；用于时间止损的持仓天数计数。"""
    today = today or cn_now().date()
    return trading_days_between(buy_date, today)


def next_trading_day(d: date) -> date | None:
    import bisect

    days = _sorted_calendar()
    if not days:
        cur = d + timedelta(days=1)
        for _ in range(15):
            if _is_workday_fallback(cur):
                return cur
            cur += timedelta(days=1)
        return None
    i = bisect.bisect_right(days, d)
    if i < len(days):
        return days[i]
    cur = d + timedelta(days=1)
    for _ in range(15):
        if _is_workday_fallback(cur):
            return cur
        cur += timedelta(days=1)
    return None


def prev_trading_day(d: date) -> date | None:
    import bisect

    days = _sorted_calendar()
    if not days:
        cur = d - timedelta(days=1)
        for _ in range(15):
            if _is_workday_fallback(cur):
                return cur
            cur -= timedelta(days=1)
        return None
    i = bisect.bisect_left(days, d) - 1
    return days[i] if i >= 0 else None


def refresh_calendar() -> int:
    """强制重新拉取交易日历并刷新缓存；返回交易日条数。供运维/脚本调用。"""
    global _cached_days, _cached_mtime, _last_check_mono, _sorted_days
    _cached_days = None
    _cached_mtime = None
    _last_check_mono = 0.0
    _sorted_days = None
    days = _fetch_and_cache()
    _cached_days = None  # 强制下次重读文件
    _cached_mtime = None
    return len(_load_calendar_fresh())
