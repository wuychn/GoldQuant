"""按交易日回填东财涨停池到 store/zt_pool/。

用法：
    poetry run python -m scripts.data.backfill_zt_pool `
      --home D:\\ProgramData\\.quant --start 2020-01-01 --end 2026-09-30
"""

from __future__ import annotations

import argparse
import random
import time
from datetime import date

from common.progress_log import log_progress, log_progress_done, log_progress_error, log_progress_start
from quant.data.calendar import to_iso, trading_day_list
from quant.data.sources.interface import try_with_fallback
from quant.data.zt_pool import write_zt_pool_snapshot, zt_pool_exists
from quant.store.paths import quant_home
from scripts.cli_home import add_home_argument, home_context

_SCOPE = "backfill_zt_pool"


def main() -> None:
    ap = argparse.ArgumentParser(description="回填东财涨停池（按交易日）")
    add_home_argument(ap)
    ap.add_argument("--start", default="2020-01-01")
    ap.add_argument("--end", default=None, help="默认今天")
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--no-resume", action="store_true")
    ap.add_argument(
        "--source",
        choices=("daily", "eastmoney", "auto"),
        default="daily",
        help="daily=用不复权日线重建（可回填到 2020）；eastmoney=东财接口（目前只有当天有数）",
    )
    args = ap.parse_args()
    end = args.end or date.today().isoformat()
    with home_context(args.home):
        log_progress_start(_SCOPE, "开始", detail=f"{args.start}~{end}")
        try:
            from quant.data.zt_pool import reconstruct_zt_pool_range

            if args.source in ("daily", "auto"):
                info = reconstruct_zt_pool_range(
                    start=args.start, end=end, skip_existing=not args.no_resume
                )
                log_progress(_SCOPE, "daily reconstruct", detail=str(info))
                if args.source == "daily":
                    log_progress_done(_SCOPE, f"daily {info}")
                    return

            trading = [
                to_iso(d)
                for d in trading_day_list(
                    date.fromisoformat(args.start), date.fromisoformat(end)
                )
            ]
            n_ok = n_skip = n_fail = 0
            for i, day in enumerate(trading, 1):
                if not args.no_resume and zt_pool_exists(day):
                    n_skip += 1
                    if i % 50 == 0:
                        log_progress(_SCOPE, f"skip {i}/{len(trading)} {day}")
                    continue
                try:
                    df = try_with_fallback("market", "fetch_zt_pool_hist", date=day)
                    n = write_zt_pool_snapshot(day, df)
                    n_ok += 1
                    log_progress(_SCOPE, f"{day} {i}/{len(trading)} rows={n}")
                except Exception as e:  # noqa: BLE001
                    n_fail += 1
                    log_progress(_SCOPE, f"FAIL {day}: {type(e).__name__}: {e}")
                lo = max(10.0, float(args.interval))
                time.sleep(random.uniform(lo, lo + 2.0))
            log_progress_done(
                _SCOPE,
                f"ok={n_ok} skip={n_skip} fail={n_fail} store={quant_home() / 'store' / 'zt_pool'}",
            )
        except Exception as e:
            log_progress_error(_SCOPE, str(e))
            raise


if __name__ == "__main__":
    main()
