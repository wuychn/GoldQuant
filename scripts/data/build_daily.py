"""全量初始化离线日线库：逐只拉 stock_zh_a_hist（不复权）+ 指数 + 交易日历。

支持断点续传（跳过已落库代码）、3-5 并发、失败重试。
建议夜间执行。不要开高并发，会被东财限流。

用法：
    python -m scripts.data.build_daily --start 2021-01-01 --end 2026-07-25
    python -m scripts.data.build_daily --start 2021-01-01 --workers 3
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from quant.data.fetch import fetch_hist, fetch_index, fetch_trade_calendar
from quant.data.store import (
    daily_raw_dir,
    read_daily_raw,
    write_calendar,
    write_daily_raw,
    write_index_daily,
)
from quant.timeutil import cn_now

# 代码表来源：spot_em 当日快照（含沪深京全A）
def _all_codes() -> list[str]:
    from quant.data.fetch import fetch_spot_em

    df = fetch_spot_em()
    if df.empty:
        return []
    return df["code"].astype(str).str.strip().tolist()


def _existing_codes() -> set[str]:
    df = read_daily_raw()
    if df.empty:
        return set()
    return set(df["code"].astype(str).str.strip().unique())


def _build_one(code: str, start: str, end: str, retries: int = 2) -> tuple[str, int]:
    last_err: Exception | None = None
    for _ in range(retries + 1):
        try:
            df = fetch_hist(code, start=start, end=end, adjust="")
            if not df.empty:
                write_daily_raw(df)
            return code, len(df)
        except Exception as e:
            last_err = e
            time.sleep(1.0)
    print(f"[WARN] {code} 拉取失败: {last_err}", file=sys.stderr)
    return code, 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-01-01", help="起始日 YYYY-MM-DD")
    ap.add_argument("--end", default=None, help="结束日 YYYY-MM-DD，默认今天")
    ap.add_argument("--workers", type=int, default=3, help="并发数（建议 3-5）")
    ap.add_argument("--limit", type=int, default=None, help="只拉前 N 只（调试用）")
    ap.add_argument("--codes", default=None, help="逗号分隔的代码列表（调试用）")
    args = ap.parse_args()

    end = args.end or cn_now().strftime("%Y-%m-%d")

    # 交易日历
    try:
        cal = fetch_trade_calendar()
        write_calendar([d for d in cal if d <= end])
        print(f"交易日历: {len(cal)} 条")
    except Exception as e:
        print(f"[WARN] 交易日历拉取失败: {e}", file=sys.stderr)

    # 基准指数
    for idx in ("000300", "000905", "000852"):
        try:
            df = fetch_index(idx, start=args.start, end=end)
            write_index_daily(df)
            print(f"指数 {idx}: {len(df)} 行")
        except Exception as e:
            print(f"[WARN] 指数 {idx} 失败: {e}", file=sys.stderr)

    # 代码表
    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        try:
            codes = _all_codes()
        except Exception as e:
            print(f"[FATAL] 无法获取代码表（spot_em 失败）: {e}", file=sys.stderr)
            sys.exit(1)
    if args.limit:
        codes = codes[: args.limit]
    print(f"全A 代码数: {len(codes)}")

    done = _existing_codes()
    todo = [c for c in codes if c not in done]
    print(f"已落库: {len(done)}，待拉: {len(todo)}")

    ok = fail = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_build_one, c, args.start, end): c for c in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            code, n = fut.result()
            if n > 0:
                ok += 1
            else:
                fail += 1
            if i % 50 == 0 or i == len(todo):
                print(f"进度 {i}/{len(todo)}  ok={ok} fail={fail}  {time.time()-t0:.0f}s")

    print(f"完成: ok={ok} fail={fail}  耗时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
