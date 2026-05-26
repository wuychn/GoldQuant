"""strategy.md 段落读取。"""

from __future__ import annotations

import re

from quant.config import STRATEGY_FILE


def load_sections(*titles: str) -> str:
    text = STRATEGY_FILE.read_text(encoding="utf-8")
    parts: list[str] = []
    for title in titles:
        pattern = rf"^##\s*{re.escape(title)}\s*$"
        m = re.search(pattern, text, flags=re.MULTILINE)
        if not m:
            continue
        start = m.end()
        nxt = re.search(r"^##\s+", text[start:], flags=re.MULTILINE)
        chunk = text[start : start + nxt.start()] if nxt else text[start:]
        parts.append(f"## {title}\n{chunk.strip()}")
    return "\n\n".join(parts)
