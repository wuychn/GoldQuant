"""A 股连续竞价时段校验。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from quant.config import trading_time_checks_enabled

_CN_TZ = ZoneInfo("Asia/Shanghai")
_AM_START = (9, 30)
_AM_END = (11, 30)
_PM_START = (13, 0)
_PM_END = (15, 0)


def cn_local_now() -> datetime:
    return datetime.now(_CN_TZ)


def _hm_le(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[0] or (a[0] == b[0] and a[1] <= b[1])


def _hm_ge(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] > b[0] or (a[0] == b[0] and a[1] >= b[1])


def is_a_share_continuous_auction_window(now: datetime | None = None) -> bool:
    if not trading_time_checks_enabled():
        return True
    now = now or cn_local_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=_CN_TZ)
    else:
        now = now.astimezone(_CN_TZ)
    hm = (now.hour, now.minute)
    in_window = (_hm_ge(hm, _AM_START) and _hm_le(hm, _AM_END)) or (
        _hm_ge(hm, _PM_START) and _hm_le(hm, _PM_END)
    )
    if not in_window:
        return False
    from app.utils.common_util import _is_real_workday_single_day_api

    return _is_real_workday_single_day_api(now.date())
