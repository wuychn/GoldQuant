#!/usr/bin/env python3
"""命令行入口。5 板复盘：``python -m quant daily_decision``。"""

from __future__ import annotations

import os
import sys

for k in list(os.environ.keys()):
    if "proxy" in k.lower():
        del os.environ[k]

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from common.progress_log import configure_progress_logging


def main() -> None:
    configure_progress_logging()
    if len(sys.argv) < 2 or sys.argv[1] != "daily_decision":
        print("用法: python -m quant daily_decision [--no-push] [--dry-run]")
        sys.exit(1)
    from quant.ops.runner import run_daily_decision

    dry = "--dry-run" in sys.argv
    no_push = "--no-push" in sys.argv
    run_daily_decision(push=not no_push, dry_run=dry)


if __name__ == "__main__":
    main()
