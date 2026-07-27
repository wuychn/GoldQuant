"""push/format 格式工具单测。"""

from __future__ import annotations

from quant.push.format import (
    GREEN,
    RED,
    emoji_for_pct,
    fmt_pct,
    fmt_price,
    format_push_message,
    icon_section,
    money,
    section,
    to_float,
)


def test_to_float():
    assert to_float(None) is None
    assert to_float("") is None
    assert to_float("abc") is None
    assert to_float(0) == 0.0
    assert to_float(1.5) == 1.5
    assert to_float("1.5") == 1.5
    assert to_float("1,234.5") == 1234.5
    assert to_float("3.2%") == 3.2


def test_fmt_pct():
    assert fmt_pct(None) == "—"
    assert fmt_pct(0.32) == "+0.32%"
    assert fmt_pct(0) == "+0.00%"
    assert fmt_pct(-0.3, short=True) == "-0.3%"
    assert fmt_pct(1.0) == "+1.00%"
    assert fmt_pct("2.5", short=True) == "+2.5%"
    assert fmt_pct("abc") == "—"


def test_fmt_price():
    assert fmt_price(None) == "—"
    assert fmt_price(3123.456) == "3123.46"
    assert fmt_price("5.37") == "5.37"
    assert fmt_price("abc") == "—"


def test_emoji_for_pct():
    assert emoji_for_pct(1.0) == RED
    assert emoji_for_pct(0) == RED
    assert emoji_for_pct(-1.0) == GREEN
    assert emoji_for_pct(None) == ""
    assert emoji_for_pct("abc") == ""


def test_format_push_message():
    assert format_push_message("智能盯盘", "2026-07-27 10:00:00", "正文") == (
        "【智能盯盘】2026-07-27 10:00:00\n\n正文\n"
    )
    # body 被 strip
    assert format_push_message("X", "t", "  hi  ") == "【X】t\n\nhi\n"


def test_section_empty():
    assert section("X", []) == ""
    assert section("X", ["", "  "]) == ""
    assert section("X", ["a"]) == "【X】\na"


def test_icon_section():
    assert icon_section("📊", "指数", []) == ""
    assert icon_section("📊", "指数", ["", "  "]) == ""
    assert icon_section("📊", "指数", ["上证 3123 🔴+0.3%"]) == "📊 指数\n上证 3123 🔴+0.3%"


def test_money():
    assert money(12345.6) == "12,345.60"
    assert money("abc") == "abc"
