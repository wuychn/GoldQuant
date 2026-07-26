"""A股交易日历：基于 AKShare ``tool_trade_date_hist_sina`` 的本地缓存。

提供：
- ``is_trading_day(d)`` / ``trading_days_between(a, b)`` / ``next_trading_day(d)``
- ``trading_days_since(buy_date, today)``：用于时间止损的持仓交易日计数

数据源仅返回历史交易日；当日与未来日由 ``is_real_workday_cn`` 兜底判定
（周一至周五且非法定休日），避免在数据源未更新时把今天误判为非交易日。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from quant.store.paths import quant_home
from quant.timeutil import cn_now


def _calendar_path() -> Path:
    return quant_home() / "cache" / "trade_calendar.json"


@lru_cache(maxsize=1)
def _load_calendar() -> set[date]:
    """加载交易日集合；缓存文件不存在或拉取失败时回退到工作日近似。"""
    path = _calendar_path()
    days: set[date] = set()
    if path.is_file():
        try:
            arr = json.loads(path.read_text(encoding="utf-8"))
            for s in arr:
                days.add(date.fromisoformat(str(s)[:10]))
        except (json.JSONDecodeError, ValueError, OSError):
            days = set()
    if not days:
        days = _fetch_and_cache()
    return days


_calendar_mtime: float | None = None


def _load_calendar_fresh() -> set[date]:
    """带文件 mtime 失效的加载：跨日长跑或外部 refresh 后自动重读。"""
    global _calendar_mtime
    path = _calendar_path()
    try:
        mtime = path.stat().st_mtime if path.is_file() else None
    except OSError:
        mtime = None
    if mtime != _calendar_mtime:
        _load_calendar.cache_clear()
        _calendar_mtime = mtime
    return _load_calendar()


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
    if isinstance(v, date):
        return v
    s = str(v).strip()[:10]
    if not s:
        return None
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
    if d in cal:
        return True
    if d > max(cal, default=date.min):
        return _is_workday_fallback(d)
    # 历史日不在日历中 → 确定不是交易日
    if d < min(cal, default=date.max):
        return False
    return d in cal


def trading_days_between(start: date, end: date) -> int:
    """(start, end] 区间内的交易日数（不含 start，含 end）。"""
    if end <= start:
        return 0
    n = 0
    cur = start + timedelta(days=1)
    while cur <= end:
        if is_trading_day(cur):
            n += 1
        cur += timedelta(days=1)
    return n


def trading_days_since(buy_date: date, today: date | None = None) -> int:
    """买入日之后到 today（含）的交易日数；用于时间止损的持仓天数计数。"""
    today = today or cn_now().date()
    return trading_days_between(buy_date, today)


def next_trading_day(d: date) -> date | None:
    cal = _load_calendar_fresh()
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
    cal = _load_calendar_fresh()
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
    """强制重新拉取交易日历并刷新缓存；返回交易日条数。供运维/脚本调用。"""
    _load_calendar.cache_clear()
    days = _fetch_and_cache()
    _load_calendar.cache_clear()
    return len(days)
