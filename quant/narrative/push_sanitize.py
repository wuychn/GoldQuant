"""飞书正文清洗。"""

from __future__ import annotations

import re

_RE_MULTI_NL = re.compile(r"\n{3,}")


def sanitize_feishu_body(body: str, *, push_timestamp: str = "") -> str:
    text = (body or "").strip()
    text = text.replace("\r\n", "\n")
    text = _RE_MULTI_NL.sub("\n\n", text)
    return text.strip()
