"""飞书正文格式：简洁、可读、专业（参考 r1 风格）。

提供两类工具：
- 外壳 / 小节：``format_push_message``、``section``、``icon_section``
- 数值格式：``emoji_for_pct``（🔴红涨 / 🟢绿跌）、``fmt_pct``、``fmt_price``、``money``
"""

from __future__ import annotations

# A 股惯例：红涨绿跌
RED = "🔴"
GREEN = "🟢"

# 章节图标
ICON_MARKET = "📊"
ICON_HOLD = "💼"
ICON_WATCH = "👀"
ICON_ACCOUNT = "💰"
ICON_ORDER = "🧾"
ICON_FILL = "✅"
ICON_TIP = "📌"
ICON_NEWS = "📡"

# 指数代码 → 简称
INDEX_LABELS = {
    "000001": "上证",
    "399001": "深证",
    "399006": "创业板",
}


def to_float(v: object) -> float | None:
    """容错转 float：None / 空串 / 无法解析 → None。

    支持 ``1.23``、``"1.23"``、``"1,234.5"``（千分位）、``"3.2%"``（尾部百分号）。
    """
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        pass
    s = str(v).strip().replace(",", "").rstrip("%")
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def format_push_message(label: str, timestamp: str, body: str) -> str:
    """统一外壳：【标签】时间 + 正文。"""
    body = (body or "").strip()
    return f"【{label}】{timestamp}\n\n{body}\n"


def section(title: str, lines: list[str]) -> str:
    """小节块；无内容则返回空串。"""
    clean = [ln for ln in lines if ln and str(ln).strip()]
    if not clean:
        return ""
    return f"【{title}】\n" + "\n".join(clean)


def icon_section(icon: str, title: str, lines: list[str]) -> str:
    """带图标的小节块（``📊 指数``）；无内容则返回空串。"""
    clean = [ln for ln in lines if ln and str(ln).strip()]
    if not clean:
        return ""
    return f"{icon} {title}\n" + "\n".join(clean)


def kv_line(key: str, value: object) -> str:
    return f"{key} {value}"


def emoji_for_pct(pct: object) -> str:
    """涨跌 emoji：>=0 红、<0 绿、None / 无法解析 → 空。"""
    p = to_float(pct)
    if p is None:
        return ""
    return RED if p >= 0 else GREEN


def fmt_pct(pct: object, *, short: bool = False) -> str:
    """百分比：带符号；None / 无法解析 → ``—``。"""
    p = to_float(pct)
    if p is None:
        return "—"
    sign = "+" if p >= 0 else ""
    return f"{sign}{p:.1f}%" if short else f"{sign}{p:.2f}%"


def fmt_price(px: object) -> str:
    """价格：2 位小数；None / 无法解析 → ``—``。"""
    p = to_float(px)
    if p is None:
        return "—"
    return f"{p:.2f}"


def money(v: object) -> str:
    try:
        return f"{float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v)
