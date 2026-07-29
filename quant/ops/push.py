"""飞书推送封装 + 本地 review 落盘。"""

from __future__ import annotations

from pathlib import Path

from quant.push.feishu import get_token, send_msg
from quant.push.format import format_push_message
from quant.store.paths import daily_dir, reports_dir
from common.timeutil import cn_datetime_str


def save_review(mode: str, text: str, *, date_str: str | None = None) -> Path:
    root = daily_dir(date_str) / "review"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{mode}.md"
    path.write_text(text, encoding="utf-8")
    return path


def push_text(label: str, body: str, *, mode: str, push: bool = True) -> str:
    """格式化 → 落盘 → 可选推送。返回完整消息。"""
    ts = cn_datetime_str()
    msg = format_push_message(label, ts, body)
    save_review(mode, msg)
    try:
        (reports_dir("ops") / f"{mode}_{ts[:10]}.txt").write_text(msg, encoding="utf-8")
    except Exception:
        pass
    if push:
        try:
            send_msg(msg, get_token())
        except Exception as e:
            print(f"[WARN] 飞书推送失败: {e}")
    return msg
