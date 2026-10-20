"""盘中：按计划买卖并推飞书。

    python -m scripts.tactic.intraday
    python -m scripts.tactic.intraday --loop   # 09:30–15:00 每 3 分钟
"""

from __future__ import annotations

import argparse
import time

from quant.tactic.engine import run_intraday
from scripts.cli_home import add_home_argument, home_context
from common.timeutil import cn_now, intraday_minutes_since_open


def main() -> None:
    ap = argparse.ArgumentParser(description="5板战术盘中")
    add_home_argument(ap)
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval", type=int, default=180)
    args = ap.parse_args()
    with home_context(args.home):
        while True:
            now = cn_now()
            mins = intraday_minutes_since_open(now)
            if mins is None and not args.loop:
                out = run_intraday(push=not args.no_push, now=now)
                print(out["body"])
                return
            if mins is not None or now.hour * 100 + now.minute < 930:
                if mins is not None or (now.hour == 9 and now.minute >= 15):
                    out = run_intraday(push=not args.no_push, now=now)
                    print(out.get("actions") or "无动作", flush=True)
            if not args.loop:
                return
            if now.hour > 15 or (now.hour == 15 and now.minute >= 5):
                return
            time.sleep(max(30, int(args.interval)))


if __name__ == "__main__":
    main()
