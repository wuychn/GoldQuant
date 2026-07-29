"""批量构建 fundamental_pit 财务表（公告日 PIT 闸门 + 分批/重试/断点续跑）。

用法：
    python -m scripts.data.build_fundamental_pit
    python -m scripts.data.build_fundamental_pit --codes 000001,600519 --limit 50
    python -m scripts.data.build_fundamental_pit --resume --batch-size 20 --sleep 0.5
"""

from __future__ import annotations

import argparse
import sys
import time

from quant.data.adjust import load_adjusted_daily
from quant.data.fundamental_pit import refresh_fundamental_pit_incremental


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default=None, help="逗号分隔；默认 universe 全量")
    ap.add_argument("--limit", type=int, default=0, help="最多拉取只数（0=不限）")
    ap.add_argument("--batch-size", type=int, default=50, help="每批写入只数")
    ap.add_argument("--sleep", type=float, default=0.3, help="每只间隔秒数（限流）")
    ap.add_argument("--retries", type=int, default=3, help="单只失败重试次数")
    ap.add_argument("--resume", action="store_true", help="跳过 parquet 已有代码")
    args = ap.parse_args()

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        daily = load_adjusted_daily()
        codes = sorted(daily["code"].astype(str).unique().tolist())
    if args.limit > 0:
        codes = codes[: args.limit]
    if args.resume:
        from quant.data.fundamental_pit import codes_in_pit_table

        done = codes_in_pit_table()
        codes = [c for c in codes if c not in done]
        print(f"断点续跑：跳过已有 {len(done)} 只，待拉 {len(codes)} 只")

    ok, fail = refresh_fundamental_pit_incremental(
        limit=args.limit if args.limit > 0 else 0,
        sleep=args.sleep,
        retries=args.retries,
        new_codes_only=bool(args.resume),
        codes=codes,
    )
    print(f"fundamental_pit 完成 | 成功 {ok} | 失败 {fail} | 共 {len(codes)} 只目标")


if __name__ == "__main__":
    main()
