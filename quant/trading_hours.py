"""A 股连续竞价时段（北京时间）：用于限制程序化买卖触发时间。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from quant.config import trading_time_checks_enabled

_CN_TZ = ZoneInfo("Asia/Shanghai")

# 上交所/深交所连续竞价：上午 9:30～11:30，下午 13:00～15:00（含起止时刻）
_AM_START = (9, 30)
_AM_END = (11, 30)
_PM_START = (13, 0)
_PM_END = (15, 0)


def cn_local_now() -> datetime:
    """当前北京时间（带 Asia/Shanghai 时区）。"""
    return datetime.now(_CN_TZ)


def _hm_le(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[0] or (a[0] == b[0] and a[1] <= b[1])


def _hm_ge(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] > b[0] or (a[0] == b[0] and a[1] >= b[1])


def is_a_share_continuous_auction_window(
    now: datetime | None = None,
    *,
    enforce_real_workday: bool | None = None,
) -> bool:
    """是否允许程序化触发买卖（由 ``quant/rules_config.yml`` 的 ``trading.time_validation_enabled`` 控制；

    兼容旧键 ``trading.enforce_real_workday``。未配置时默认严格。

    - **False**：始终返回 ``True``，不限制工作日与连续竞价时段，便于任意时刻联调/回测。
    - **True**（默认）：须同时满足——北京时间连续竞价 9:30～11:30、13:00～15:00，
      且 ``app.utils.common_util._is_real_workday_single_day_api`` 对当日为真实交易日。

    参数 ``enforce_real_workday`` 为 ``None`` 时读取 YAML；显式传入 ``True``/``False`` 可覆盖配置（单测等）。

    - ``now`` 在非严格模式下可省略；严格模式下 ``None`` 表示当前北京时间。
    """
    if enforce_real_workday is None:
        enforce_real_workday = trading_time_checks_enabled()

    if not enforce_real_workday:
        return True

    if now is None:
        now = cn_local_now()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=_CN_TZ)
    else:
        now = now.astimezone(_CN_TZ)

    hm = (now.hour, now.minute)
    in_auction = (_hm_ge(hm, _AM_START) and _hm_le(hm, _AM_END)) or (
        _hm_ge(hm, _PM_START) and _hm_le(hm, _PM_END)
    )
    if not in_auction:
        return False

    from app.utils.common_util import _is_real_workday_single_day_api

    return _is_real_workday_single_day_api(now.date())
