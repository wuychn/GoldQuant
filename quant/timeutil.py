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


def ensure_cn_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=CN_TZ)
    return dt.astimezone(CN_TZ)
