"""列出 FastAPI 应用全部路由，用于与 routes_before.txt 对比。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.main import create_app  # noqa: E402


def main() -> None:
    app = create_app()
    rows = []
    for r in app.routes:
        methods = getattr(r, "methods", None) or set()
        path = getattr(r, "path", "")
        for m in sorted(methods - {"HEAD"}):
            if m:
                rows.append(f"{{{m!r}}} {path}")
    for line in sorted(rows):
        print(line)


if __name__ == "__main__":
    main()
