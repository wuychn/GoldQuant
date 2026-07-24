"""新闻推送正文格式化。"""

from __future__ import annotations

import re

_RE_PAREN_WRAP = re.compile(r"^[（(](.+)[）)]$")
_RE_NUM_LINE = re.compile(r"^(\d+)[\.、]\s*")


def strip_wrapped_parens(text: str) -> str:
    """去掉整条被括号包裹的要点，如 `（某某新闻）` → `某某新闻`。"""
    s = text.strip()
    m = _RE_PAREN_WRAP.match(s)
    if m:
        return m.group(1).strip()
    # 首尾各一对括号
    if s.startswith("（") and s.endswith("）") and s.count("（") == 1:
        return s[1:-1].strip()
    if s.startswith("(") and s.endswith(")") and s.count("(") == 1:
        return s[1:-1].strip()
    return s


def format_news_for_push(raw: str, *, max_items: int = 50) -> str:
    """清洗 LLM 新闻输出：去括号包裹、去掉不完整末条、保留综合解读。"""
    if not raw:
        return ""
    body = raw.strip()
    summary_part = ""
    if "综合解读" in body:
        head, tail = body.split("综合解读", 1)
        body = head
        summary_part = "综合解读" + tail.strip()

    lines_out: list[str] = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _RE_NUM_LINE.match(line)
        if not m:
            lines_out.append(line)
            continue
        num = int(m.group(1))
        if num > max_items:
            continue
        rest = line[m.end() :].strip()
        rest = strip_wrapped_parens(rest)
        if not rest:
            continue
        lines_out.append(f"{num}. {rest}")

    # 仅去掉最后一条编号要点（若缺句末标点，多为 LLM 输出被截断）
    if lines_out:
        last = lines_out[-1]
        lm = _RE_NUM_LINE.match(last)
        if lm:
            content = last[lm.end() :].strip()
            if content and content[-1] not in "。！？；…":
                lines_out.pop()

    out = "\n".join(lines_out)
    if summary_part:
        summary_part = strip_wrapped_parens(summary_part.replace("综合解读", "", 1).lstrip("：:"))
        out = f"{out}\n\n综合解读：{summary_part.strip()}" if out else f"综合解读：{summary_part.strip()}"
    return out.strip()
