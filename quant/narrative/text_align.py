"""推送文案列宽工具（clip / pad，供个别字段截断使用）。"""

from __future__ import annotations

import unicodedata


def display_width(text: str) -> int:
    w = 0
    for ch in str(text):
        if unicodedata.east_asian_width(ch) in ("F", "W"):
            w += 2
        elif ord(ch) >= 0x1F300:
            w += 2
        else:
            w += 1
    return w


def clip_display(text: str, max_width: int) -> str:
    if max_width <= 0:
        return ""
    out: list[str] = []
    w = 0
    for ch in str(text):
        cw = 2 if unicodedata.east_asian_width(ch) in ("F", "W") or ord(ch) >= 0x1F300 else 1
        if w + cw > max_width:
            break
        out.append(ch)
        w += cw
    return "".join(out)


def pad_display(text: str, width: int) -> str:
    s = str(text)
    pad = width - display_width(s)
    if pad <= 0:
        return s
    return s + (" " * pad)


def max_display_width(values: list[str], *, floor: int = 0, cap: int | None = None) -> int:
    w = max((display_width(v) for v in values), default=0)
    w = max(w, floor)
    if cap is not None:
        w = min(w, cap)
    return w
