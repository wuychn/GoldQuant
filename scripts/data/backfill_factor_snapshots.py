"""历史回填 theme 因子 PIT 快照。

基本面请用 ``scripts.data.build_fundamental_pit``（唯一来源，TTM + 公告日 PIT）。

用法：
    python -m scripts.data.build_fundamental_pit
    python -m scripts.data.backfill_factor_snapshots --start 2024-01-01 --end 2024-03-31
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from quant.data.calendar import is_trading_day, to_iso, trading_day_list


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    args = ap.parse_args()
    dates = [
        to_iso(d)
        for d in trading_day_list(date.fromisoformat(args.start), date.fromisoformat(args.end))
        if is_trading_day(d)
    ]
    print(f"回填 theme 快照 {len(dates)} 个交易日")
    from quant.data.adjust import load_adjusted_daily
    from quant.data.factor_capture import capture_theme_mom

    daily = load_adjusted_daily(start=args.start, end=args.end)
    for d in dates:
        day = daily[daily["date"] == d]
        if day.empty:
            continue
        n = capture_theme_mom(d, day)
        print(f"{d}: theme={n}")


if __name__ == "__main__":
    main()
