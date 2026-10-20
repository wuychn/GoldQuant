"""补拉当日涨停族。``update_daily`` 已包含这一步。

用法：
    python -m scripts.data.capture_session_archive
    python -m scripts.data.capture_session_archive --date 2026-09-30
    python -m scripts.data.capture_session_archive --closed-session
"""

from __future__ import annotations

import argparse
import sys
from datetime import date as date_cls

from common.timeutil import cn_now
from quant.data.calendar import closed_session_as_of, is_trading_day, to_iso
from scripts.cli_home import add_home_argument, home_context


def main() -> None:
    ap = argparse.ArgumentParser(description="补拉当日涨停池、炸板、跌停、昨涨停")
    add_home_argument(ap)
    ap.add_argument("--date", default=None, help="YYYY-MM-DD，默认今天")
    ap.add_argument("--closed-session", action="store_true", help="按最近已收盘日（16:00 前用上一交易日）")
    args = ap.parse_args()
    with home_context(args.home):
        if args.date:
            today = to_iso(args.date)
        elif args.closed_session:
            closed = closed_session_as_of(close_hour=16)
            if closed is None:
                print("[SKIP] 无已收盘交易日")
                return
            today = closed.isoformat()
            print(f"closed-session as_of={today}", flush=True)
        else:
            today = cn_now().date().isoformat()
        if not is_trading_day(date_cls.fromisoformat(today)):
            print(f"[SKIP] {today} 非交易日")
            return
        from quant.data.session_archive import capture_session_extras

        try:
            cap = capture_session_extras(today)
            print(f"session @ {today}: {cap}", flush=True)
        except Exception as e:
            print(f"[FAIL] session_archive: {e}", file=sys.stderr)
            raise SystemExit(1) from e


if __name__ == "__main__":
    main()
