"""飞书消息格式。"""

from __future__ import annotations


def format_push_message(label: str, timestamp: str, body: str) -> str:
    return f"【{label}】{timestamp}\n\n{body.strip()}\n"
