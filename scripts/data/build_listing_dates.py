"""批量构建上市日表（daily 首条 + jbxx 覆盖）。

用法：
    python -m scripts.data.build_listing_dates
"""

from __future__ import annotations

import sys

from common.progress_log import log_progress, log_progress_done, log_progress_error, log_progress_start
from quant.data.adjust import load_adjusted_daily
from quant.data.listing import build_listing_map_from_daily, merge_jbxx_listing, write_listing_table

_SCOPE = "build_listing_dates"


def main() -> None:
    log_progress_start(_SCOPE, "开始")
    try:
        log_progress(_SCOPE, "加载后复权日线 …")
        daily = load_adjusted_daily()
        if daily is None or daily.empty:
            log_progress_error(_SCOPE, "失败", detail="daily_raw/后复权为空")
            sys.exit(1)
        log_progress(_SCOPE, "构建上市日映射 …", detail=f"{daily['code'].nunique()} 码")
        mp = merge_jbxx_listing(build_listing_map_from_daily(daily))
        write_listing_table(mp)
        log_progress_done(_SCOPE, "成功", detail=f"listing_dates → {len(mp)} 只")
    except Exception as e:
        log_progress_error(_SCOPE, "失败", detail=f"{type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    main()
