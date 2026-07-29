"""A 股业务统一使用 Asia/Shanghai 时区。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")


def cn_now() -> datetime:
    return datetime.now(CN_TZ)


def cn_today() -> date:
    return cn_now().date()


def cn_date_str(dt: datetime | None = None) -> str:
    return (dt or cn_now()).strftime("%Y-%m-%d")


def cn_time_str(dt: datetime | None = None) -> str:
    return (dt or cn_now()).strftime("%H:%M:%S")


def cn_datetime_str(dt: datetime | None = None) -> str:
    return (dt or cn_now()).strftime("%Y-%m-%d %H:%M:%S")


def parse_cn_datetime_str(s: str) -> datetime | None:
    """解析 ``yyyy-MM-dd HH:mm:ss`` 或 ISO 带时区字符串；失败返回 None。"""
    text = str(s).strip()
    if not text:
        return None
    try:
        return ensure_cn_tz(datetime.fromisoformat(text))
    except ValueError:
        pass
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=CN_TZ)
    except ValueError:
        return None


def ensure_cn_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=CN_TZ)
    return dt.astimezone(CN_TZ)


def intraday_minutes_since_open(dt: datetime | None = None) -> int | None:
    """连续竞价自 9:30 起算的盘中分钟数；午休暂停累计，15:00 后返回 None。"""
    ref = ensure_cn_tz(dt or cn_now())
    open_am = ref.replace(hour=9, minute=30, second=0, microsecond=0)
    close_am = ref.replace(hour=11, minute=30, second=0, microsecond=0)
    open_pm = ref.replace(hour=13, minute=0, second=0, microsecond=0)
    close_pm = ref.replace(hour=15, minute=0, second=0, microsecond=0)
    if ref < open_am or ref > close_pm:
        return None
    if ref <= close_am:
        return int((ref - open_am).total_seconds() // 60)
    if ref < open_pm:
        return int((close_am - open_am).total_seconds() // 60)
    morning = int((close_am - open_am).total_seconds() // 60)
    return morning + int((ref - open_pm).total_seconds() // 60)


def format_minutes_since_open_phrase(minutes: int | None) -> str:
    """推送/叙述用：距 9:30 开盘的时间表述。"""
    if minutes is None:
        return "非连续竞价时段"
    if minutes <= 0:
        return "刚开盘"
    if minutes < 60:
        return f"开盘约{minutes}分钟"
    hours = minutes // 60
    mins = minutes % 60
    if mins == 0:
        return f"开盘约{hours}小时"
    return f"开盘约{hours}小时{mins}分钟"


def intraday_session_time_line(dt: datetime | None = None) -> str:
    """智能盯盘写作参考：精确盘中时刻，避免 LLM 误写「开盘半小时」。"""
    ref = ensure_cn_tz(dt or cn_now())
    clock = ref.strftime("%H:%M")
    minutes = intraday_minutes_since_open(ref)
    phrase = format_minutes_since_open_phrase(minutes)
    if minutes is not None and minutes < 30:
        guard = "（10:00 才约满30分钟，勿写「开盘半小时」）"
    else:
        guard = ""
    return f"盘中时刻：{clock}，距9:30连续竞价{phrase}{guard}"


def trading_minutes_between(start: datetime, end: datetime) -> float:
    """两时刻之间连续竞价累计分钟数（剔除午休与非交易时段）。"""
    from datetime import time as dt_time

    a = ensure_cn_tz(start)
    b = ensure_cn_tz(end)
    if b <= a:
        return 0.0

    def _session_bounds(d: date) -> tuple[datetime, datetime, datetime, datetime]:
        return (
            datetime.combine(d, dt_time(9, 30), CN_TZ),
            datetime.combine(d, dt_time(11, 30), CN_TZ),
            datetime.combine(d, dt_time(13, 0), CN_TZ),
            datetime.combine(d, dt_time(15, 0), CN_TZ),
        )

    def _overlap(lo: datetime, hi: datetime, seg_lo: datetime, seg_hi: datetime) -> float:
        s = max(lo, seg_lo)
        e = min(hi, seg_hi)
        if e <= s:
            return 0.0
        return (e - s).total_seconds() / 60.0

    total = 0.0
    d = a.date()
    while d <= b.date():
        open_am, close_am, open_pm, close_pm = _session_bounds(d)
        day_lo = open_am if d == a.date() else open_am
        day_hi = b if d == b.date() else close_pm
        if d == a.date():
            day_lo = a
        if d == b.date():
            day_hi = b
        total += _overlap(day_lo, day_hi, open_am, close_am)
        total += _overlap(day_lo, day_hi, open_pm, close_pm)
        d += timedelta(days=1)
    return total
