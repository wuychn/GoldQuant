"""策略文件加载与分段。"""

import re

from quant.config import STRATEGY_FILE


def load_strategy() -> str:
    try:
        return STRATEGY_FILE.read_text(encoding="utf-8")
    except OSError as e:
        return f"策略文件加载失败：{STRATEGY_FILE}（{e!r}）"


def _strategy_split_sections(full: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    matches = list(re.finditer(r"^## (.+)$", full, re.MULTILINE))
    for i, m in enumerate(matches):
        name = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(full)
        sections[name] = full[start:end].strip().strip("-").strip()
    return sections


def load_sections(*names: str) -> str:
    full = load_strategy()
    if full.startswith("策略文件加载失败"):
        return full
    sections = _strategy_split_sections(full)
    parts = [sections[n] for n in names if n in sections and sections[n]]
    return "\n\n---\n\n".join(parts)
