"""飞书正文格式：简洁、可读、专业。"""

from __future__ import annotations


def format_push_message(label: str, timestamp: str, body: str) -> str:
    """统一外壳：标题 + 时间 + 正文。"""
    body = (body or "").strip()
    return f"{label}\n{timestamp}\n\n{body}\n"


def section(title: str, lines: list[str]) -> str:
    """小节块；无内容则返回空串。"""
    clean = [ln for ln in lines if ln and str(ln).strip()]
    if not clean:
        return ""
    return f"【{title}】\n" + "\n".join(clean)


def kv_line(key: str, value: object) -> str:
    return f"{key} {value}"


def money(v: object) -> str:
    try:
        return f"{float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v)
