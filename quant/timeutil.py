"""A 股业务统一使用 Asia/Shanghai 时区。"""

from __future__ import annotations

from datetime import date, datetime
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
