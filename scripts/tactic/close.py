"""盘后：情绪 + 观察池 + 次日计划，推飞书。

    python -m scripts.tactic.close
    python -m scripts.tactic.close --date 2026-10-16 --no-push
"""

from __future__ import annotations

import argparse
from datetime import date

from quant.data.calendar import closed_session_as_of, to_iso
from quant.tactic.engine import run_close
from scripts.cli_home import add_home_argument, home_context


def main() -> None:
    ap = argparse.ArgumentParser(description="5板战术盘后复盘")
    add_home_argument(ap)
    ap.add_argument("--date", default=None)
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args()
    with home_context(args.home):
        if args.date:
            as_of = to_iso(args.date)
        else:
            closed = closed_session_as_of(close_hour=16)
            if closed is None:
                raise SystemExit("无已收盘交易日")
            as_of = closed.isoformat()
        out = run_close(as_of, push=not args.no_push)
        print(out["body"])


if __name__ == "__main__":
    main()
