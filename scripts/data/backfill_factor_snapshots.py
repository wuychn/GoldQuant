"""历史回填因子 PIT 快照（hot/flow/fundamentals/theme）。

用法：
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
    print(f"回填 {len(dates)} 个交易日（需本地 daily_raw 已覆盖）")
    print("[INFO] 历史 hot/flow 需当日 API 或存档；本脚本对已有 daily 重建 fundamentals/theme 代理")
    from quant.data.adjust import load_adjusted_daily
    from quant.data.factor_capture import capture_fundamentals, capture_theme_mom
    from quant.data.universe import universe_codes

    daily = load_adjusted_daily(start=args.start, end=args.end)
    for d in dates:
        day = daily[daily["date"] == d]
        if day.empty:
            continue
        n1 = capture_fundamentals(d, day)
        n2 = capture_theme_mom(d, day)
        print(f"{d}: fundamentals={n1} theme={n2}")


if __name__ == "__main__":
    main()
