"""Payload 日期字符串归一化（从 app.utils.common_util 抽离）。"""

from __future__ import annotations

import re
from datetime import date, datetime

from quant.data.calendar import _coerce_date, prev_trading_day

_ISO_DT_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T")
_BUCKET_COMPACT_RE = re.compile(r"^(\d{8})T(\d{2})(\d{2})$")


def should_normalize_datetime_like_string(s: str) -> bool:
    if _ISO_DT_PREFIX_RE.match(s):
        return True
    if _BUCKET_COMPACT_RE.match(s):
        return True
    if re.match(r"^\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}(:\d{2})?$", s.strip()):
        return True
    return False


def normalize_quant_datetime_string(s: str) -> str:
    s = str(s).strip()
    if not s:
        return s
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", s):
        return s
    m_space = re.match(r"^(\d{4}-\d{2}-\d{2})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?$", s)
    if m_space:
        dd, hh, mm, ss = m_space.group(1), m_space.group(2), m_space.group(3), m_space.group(4)
        sec = ss if ss else "00"
        return f"{dd} {int(hh):02d}:{mm}:{sec}"
    m = _BUCKET_COMPACT_RE.match(s)
    if m:
        d8, hh, mm = m.group(1), m.group(2), m.group(3)
        return f"{d8[:4]}-{d8[4:6]}-{d8[6:8]} {hh}:{mm}:00"
    if _ISO_DT_PREFIX_RE.match(s):
        try:
            base = s.replace("Z", "+00:00")
            if "." in base and "T" in base:
                base = base.split(".")[0]
            dt = datetime.fromisoformat(base)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return s
    return s


def yyyymmdd_to_iso(d8: str) -> str | None:
    s = str(d8).strip().replace("-", "").replace("/", "")
    if len(s) < 8 or not s[:8].isdigit():
        return None
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def oldest_trading_day_in_window(anchor: date, n: int) -> date | None:
    """含 anchor 在内向前 n 个交易日的最早一天。"""
    if n <= 0:
        return None
    cur = anchor
    for _ in range(n - 1):
        prev = prev_trading_day(cur)
        if prev is None:
            return None
        cur = prev
    return cur
