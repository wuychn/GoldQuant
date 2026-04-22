"""比较 routes_before.txt 与 routes_after.txt 中 /api/v1 路由集合。"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

API_LINE = re.compile(r"^(\{[^}]+\})\s+(/api/v1/\S*)\s*$")


def load_api_routes(path: Path) -> set[str]:
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        m = API_LINE.match(line)
        if m:
            out.add(line)
    return out


def main() -> None:
    before = load_api_routes(ROOT / "routes_before.txt")
    after = load_api_routes(ROOT / "routes_after.txt")
    print("before count", len(before))
    print("after count", len(after))
    if before == after:
        print("OK: 集合完全一致")
        return
    print("仅 before:", sorted(before - after))
    print("仅 after:", sorted(after - before))


if __name__ == "__main__":
    main()
