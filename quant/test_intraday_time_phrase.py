"""盘中时刻表述：避免误写「开盘半小时」。"""

from __future__ import annotations

from datetime import datetime

from quant.narrative.push_sanitize import sanitize_feishu_body
from quant.timeutil import (
    CN_TZ,
    format_minutes_since_open_phrase,
    intraday_minutes_since_open,
    intraday_session_time_line,
)


def _at(h: int, m: int) -> datetime:
    return datetime(2026, 6, 15, h, m, 0, tzinfo=CN_TZ)


def test_minutes_since_open_at_937() -> None:
    assert intraday_minutes_since_open(_at(9, 37)) == 7


def test_minutes_since_open_at_947() -> None:
    assert intraday_minutes_since_open(_at(9, 47)) == 17


def test_session_time_line_warns_before_30min() -> None:
    line = intraday_session_time_line(_at(9, 37))
    assert "09:37" in line
    assert "约7分钟" in line
    assert "勿写「开盘半小时」" in line


def test_sanitize_replaces_false_half_hour_before_10() -> None:
    body = "一、大盘概况\n开盘半小时指数走强。"
    out = sanitize_feishu_body(body, push_timestamp="2026-06-15 09:37:06")
    assert "开盘半小时" not in out
    assert "开盘约7分钟" in out


def test_sanitize_keeps_half_hour_after_1000() -> None:
    body = "开盘半小时指数走强。"
    out = sanitize_feishu_body(body, push_timestamp="2026-06-15 10:05:06")
    assert "开盘半小时" in out


def test_format_phrase() -> None:
    assert format_minutes_since_open_phrase(7) == "开盘约7分钟"
    assert format_minutes_since_open_phrase(30) == "开盘约30分钟"
