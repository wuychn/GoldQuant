"""批量构建 fundamental_pit 财务表（公告日 PIT 闸门 + 分批/重试/断点续跑）。

用法：
    python -m scripts.data.build_fundamental_pit
    python -m scripts.data.build_fundamental_pit --codes 000001,600519 --limit 50
    python -m scripts.data.build_fundamental_pit --resume --batch-size 20 --sleep 0.5
"""

from __future__ import annotations

import argparse
import sys

from common.progress_log import log_progress, log_progress_done, log_progress_error, log_progress_start
from quant.data.adjust import load_adjusted_daily
from quant.data.fundamental_pit import refresh_fundamental_pit_incremental

_SCOPE = "build_fundamental_pit"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default=None, help="逗号分隔；默认 universe 全量")
    ap.add_argument("--limit", type=int, default=0, help="最多拉取只数（0=不限）")
    ap.add_argument("--batch-size", type=int, default=50, help="每批写入只数（保留兼容，实际按库内批量写）")
    ap.add_argument("--sleep", type=float, default=0.3, help="每只间隔秒数（限流）")
    ap.add_argument("--retries", type=int, default=3, help="单只失败重试次数")
    ap.add_argument("--resume", action="store_true", help="跳过 parquet 已有代码")
    args = ap.parse_args()

    log_progress_start(
        _SCOPE,
        "开始",
        detail=f"limit={args.limit} sleep={args.sleep} retries={args.retries} resume={args.resume}",
    )
    try:
        if args.codes:
            codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        else:
            log_progress(_SCOPE, "加载后复权日线以取代码表 …")
            daily = load_adjusted_daily()
            codes = sorted(daily["code"].astype(str).unique().tolist())
        if args.limit > 0:
            codes = codes[: args.limit]
        if args.resume:
            from quant.data.fundamental_pit import codes_in_pit_table

            done = codes_in_pit_table()
            before = len(codes)
            codes = [c for c in codes if c not in done]
            log_progress(
                _SCOPE,
                "断点续跑",
                detail=f"已有 {len(done)} · 候选 {before} → 待拉 {len(codes)}",
            )
        else:
            log_progress(_SCOPE, "待拉代码", detail=f"{len(codes)} 只")

        if not codes:
            log_progress_done(_SCOPE, "成功", detail="无待拉码，跳过")
            return

        ok, fail = refresh_fundamental_pit_incremental(
            limit=args.limit if args.limit > 0 else 0,
            sleep=args.sleep,
            retries=args.retries,
            new_codes_only=bool(args.resume),
            codes=codes,
        )
        detail = f"成功 {ok} · 失败 {fail} · 目标 {len(codes)}"
        if fail > 0:
            log_progress_error(_SCOPE, "完成但有失败", detail=detail)
            sys.exit(1)
        log_progress_done(_SCOPE, "成功", detail=detail)
    except Exception as e:
        log_progress_error(_SCOPE, "失败", detail=f"{type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    main()
