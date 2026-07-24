"""防御板块识别。"""

from __future__ import annotations

from quant.config import load_r2_config


def is_defensive_sector(name: str) -> bool:
    n = str(name).strip()
    if not n:
        return False
    blocklist = load_r2_config().get("sector", {}).get("defensive_names") or []
    for d in blocklist:
        d = str(d).strip()
        if d and (n == d or d in n or n in d):
            return True
    return False
