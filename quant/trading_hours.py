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
_LATE_SESSION_DEFAULT = (14, 30)


def parse_hhmm(text: str, default: tuple[int, int] = _LATE_SESSION_DEFAULT) -> tuple[int, int]:
    try:
        parts = str(text).strip().split(":")
        if len(parts) >= 2:
            return int(parts[0]), int(parts[1])
    except (TypeError, ValueError, AttributeError):
        pass
    return default


def is_at_or_after_hhmm(cutoff: tuple[int, int], now: datetime | None = None) -> bool:
    now = now or cn_local_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=_CN_TZ)
    else:
        now = now.astimezone(_CN_TZ)
    hm = (now.hour, now.minute)
    return _hm_ge(hm, cutoff)


def late_session_cutoff() -> tuple[int, int]:
    """趋势类卖点最终确认时刻，见 quant.yml gates.sell.late_session_after。"""
    from quant.config import load_gates_config

    sell_cfg = load_gates_config().get("sell") or {}
    raw = sell_cfg.get("late_session_after", "14:30")
    if raw in (None, "", False):
        return (0, 0)
    return parse_hhmm(str(raw))


def is_late_session_for_trend_sell(now: datetime | None = None) -> bool:
    """第 3 次（最终）趋势类卖出确认须在此时间之后。"""
    cutoff = late_session_cutoff()
    if cutoff == (0, 0):
        return True
    return is_at_or_after_hhmm(cutoff, now)


def sell_kinds_requiring_late_final() -> set[str]:
    """须「神奇2点30」最终确认的卖出 signal_kind 集合。"""
    from quant.config import load_gates_config

    sell_cfg = load_gates_config().get("sell") or {}
    if sell_cfg.get("late_session_final_only") is False:
        return set()
    raw = sell_cfg.get("late_session_kinds") or ["破5日线", "趋势衰竭", "评分走弱"]
    return {str(x).strip() for x in raw if str(x).strip()}


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
