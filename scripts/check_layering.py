#!/usr/bin/env python3
"""分层守卫：断言包依赖方向 ``common ← quant ← {app, scripts}``。

- `common/` 不得 import app 或 quant（最底层，零业务依赖）
- `quant/` 不得 import app（中间层，不反向依赖 web/服务层）

用行首 import 正则匹配，避免误报 docstring/注释里的文字。
失败退出非 0，便于接入 CI / 本地自检。

用法：python -m scripts.check_layering
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

# 行首（允许缩进）的 from/import app|quant 语句
_APP_IMPORT = re.compile(r"^\s*(from\s+app\b|import\s+app\b)", re.MULTILINE)
_QUANT_IMPORT = re.compile(r"^\s*(from\s+quant\b|import\s+quant\b)", re.MULTILINE)


def _scan(dirpath: str, patterns: list[re.Pattern[str]], label: str) -> int:
    bad: list[str] = []
    root = _ROOT / dirpath
    if not root.is_dir():
        print(f"[SKIP] {label}（目录不存在）")
        return 0
    for p in root.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(_ROOT).as_posix()
        text = p.read_text(encoding="utf-8", errors="ignore")
        for pat in patterns:
            for m in pat.finditer(text):
                lineno = text.count("\n", 0, m.start()) + 1
                line = text.splitlines()[lineno - 1].strip()
                bad.append(f"{rel}:{lineno}: {line}")
    if bad:
        print(f"[FAIL] {label}（{len(bad)} 处违规）:")
        for b in bad:
            print(f"  {b}")
    else:
        print(f"[OK]   {label}")
    return len(bad)


def main() -> int:
    n = 0
    n += _scan("common", [_APP_IMPORT, _QUANT_IMPORT], "common 不得 import app/quant")
    n += _scan("quant", [_APP_IMPORT], "quant 不得 import app")
    if n:
        print(f"\n分层守卫失败：{n} 处违规。依赖方向应为 common ← quant ← {{app, scripts}}。")
    else:
        print("\n分层守卫通过。")
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
