"""批量构建上市日表（daily 首条 + jbxx 覆盖）。

用法：
    python -m scripts.data.build_listing_dates
"""

from __future__ import annotations

from quant.data.adjust import load_adjusted_daily
from quant.data.listing import build_listing_map_from_daily, merge_jbxx_listing, write_listing_table


def main() -> None:
    daily = load_adjusted_daily()
    mp = merge_jbxx_listing(build_listing_map_from_daily(daily))
    write_listing_table(mp)
    print(f"listing_dates → {len(mp)} 只")


if __name__ == "__main__":
    main()
