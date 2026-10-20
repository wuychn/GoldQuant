"""一次补齐近 40 个交易日的 5 板观察池。不拉全市场日线。

    python -m scripts.tactic.backfill --home D:\\ProgramData\\.quant
"""

from __future__ import annotations

import argparse

from quant.data.calendar import closed_session_as_of, to_iso
from quant.tactic.capture import backfill
from scripts.cli_home import add_home_argument, home_context


def main() -> None:
    ap = argparse.ArgumentParser(description="补 5 板观察池")
    add_home_argument(ap)
    ap.add_argument("--date", default=None)
    ap.add_argument("--sessions", type=int, default=40)
    args = ap.parse_args()
    with home_context(args.home):
        if args.date:
            as_of = to_iso(args.date)
        else:
            closed = closed_session_as_of(close_hour=16)
            if closed is None:
                raise SystemExit("无已收盘交易日")
            as_of = closed.isoformat()
        out = backfill(as_of, sessions=int(args.sessions))
        print(
            f"补数 {out['as_of']} 交易日{out['sessions']} 5板{out['codes']} 日K失败{len(out['failed'])} 涨停池无数据{len(out['skipped'])}",
            flush=True,
        )
        for line in out["failed"][:20]:
            print(line, flush=True)


if __name__ == "__main__":
    main()
