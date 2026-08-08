"""历史回填 theme 因子 PIT 快照。

基本面请用 ``scripts.data.build_fundamental_pit``（唯一来源，TTM + 公告日 PIT）。

用法：
    python -m scripts.data.backfill_factor_snapshots --start 2024-01-01 --end 2024-03-31
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date

from common.progress_log import (
    log_progress,
    log_progress_count,
    log_progress_done,
    log_progress_error,
    log_progress_start,
)
from quant.data.calendar import is_trading_day, to_iso, trading_day_list
from scripts.cli_home import add_home_argument, home_context

_SCOPE = "backfill_factor_snapshots"


def main() -> None:
    ap = argparse.ArgumentParser(description="历史回填 theme 因子 PIT 快照")
    add_home_argument(ap)
    ap.add_argument("--start", required=True, help="回填起始日 YYYY-MM-DD（含）")
    ap.add_argument("--end", required=True, help="回填结束日 YYYY-MM-DD（含）")
    args = ap.parse_args()

    with home_context(args.home):
        log_progress_start(_SCOPE, "开始", detail=f"{args.start} ~ {args.end}")
        try:
            dates = [
                to_iso(d)
                for d in trading_day_list(date.fromisoformat(args.start), date.fromisoformat(args.end))
                if is_trading_day(d)
            ]
            if not dates:
                log_progress_error(_SCOPE, "失败", detail="区间内无交易日")
                sys.exit(1)
            log_progress(_SCOPE, "待回填交易日", detail=str(len(dates)))
            from quant.data.adjust import load_adjusted_daily
            from quant.data.factor_capture import capture_theme_mom

            log_progress(_SCOPE, "加载后复权日线 …")
            daily = load_adjusted_daily(start=args.start, end=args.end)
            ok = skip = fail = 0
            t0 = time.time()
            for i, d in enumerate(dates, 1):
                day = daily[daily["date"] == d]
                if day.empty:
                    skip += 1
                    log_progress(_SCOPE, "跳过空日", detail=d)
                else:
                    try:
                        n = capture_theme_mom(d, day)
                        ok += 1
                        log_progress(_SCOPE, "日完成", detail=f"{d} theme={n}")
                    except Exception as e:  # noqa: BLE001
                        fail += 1
                        log_progress_error(_SCOPE, "日失败", detail=f"{d}: {type(e).__name__}: {e}")
                if i == 1 or i % 20 == 0 or i == len(dates):
                    log_progress_count(
                        _SCOPE,
                        "进度",
                        i,
                        len(dates),
                        detail=f"ok={ok} skip={skip} fail={fail} {time.time() - t0:.0f}s",
                    )
            detail = f"ok={ok} skip={skip} fail={fail} / {len(dates)} 日"
            if fail > 0:
                log_progress_error(_SCOPE, "完成但有失败", detail=detail)
                sys.exit(1)
            log_progress_done(_SCOPE, "成功", detail=detail)
        except Exception as e:
            log_progress_error(_SCOPE, "失败", detail=f"{type(e).__name__}: {e}")
            raise


if __name__ == "__main__":
    main()
