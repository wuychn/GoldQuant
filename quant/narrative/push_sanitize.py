"""飞书推送正文用语清洗：去掉 LLM 误带的内部标签前缀。"""

from __future__ import annotations

import re

# 误带入正文的内部前缀 / 字段名
_STRIP_PREFIXES = (
    "研判中的",
    "研判要点中的",
    "研判要点：",
    "研判要点:",
    "写作参考中的",
    "程序结论中的",
)
_RE_INTERNAL_PHRASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"当日涨幅榜概念"), "当日涨幅靠前的概念"),
    (re.compile(r"当日资金流入榜概念"), "当日资金流入靠前的概念"),
    (re.compile(r"【研判要点[^】]*】"), ""),
    (re.compile(r"【历史叙述参考[^】]*】"), ""),
    (re.compile(r"【接口数据\s*JSON】"), ""),
    (re.compile(r"<<<[^>]+>>>"), ""),
]
_RE_BLANK_LINES = re.compile(r"\n{3,}")


def sanitize_feishu_body(text: str) -> str:
    if not text or not text.strip():
        return text
    out = text
    for p in _STRIP_PREFIXES:
        out = out.replace(p, "")
    for pat, repl in _RE_INTERNAL_PHRASES:
        out = pat.sub(repl, out)
    return _RE_BLANK_LINES.sub("\n\n", out).strip()
