"""全量初始化离线日线库：逐只拉 stock_zh_a_hist（不复权）+ 指数 + 交易日历。

支持断点续传（跳过已落库代码）、3-5 并发。失败重试/退避由 fetch_hist 内部 ``_retry`` 兜底。
建议夜间执行。不要开高并发，会被东财限流。

用法：
    python -m scripts.data.build_daily --start 2021-01-01 --end 2026-07-25
    python -m scripts.data.build_daily --start 2021-01-01 --workers 3
    # 补日期缺口（对所有代码强制拉一段，write 按 code+date 去重不重复）：
    python -m scripts.data.build_daily --start 2026-07-20 --end 2026-07-25 --ignore-existing
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from quant.data.delist import fetch_delisted_codes, fetch_delisted_daily
from quant.data.fetch import fetch_hist, fetch_index, fetch_trade_calendar
from quant.data.store import (
    read_daily_raw,
    write_calendar,
    write_daily_raw,
    write_index_daily,
)
from common.timeutil import cn_now

# 代码表来源：stock_info_a_code_name（stockapi 稳定，不走 clist）；失败回退 spot_em
def _all_codes() -> list[str]:
    from quant.data.fetch import fetch_a_code_name, fetch_spot_em

    try:
        codes = fetch_a_code_name()
        if codes:
            return codes
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] fetch_a_code_name 失败，回退 spot_em: {e}", file=sys.stderr)
    df = fetch_spot_em()
    if df.empty:
        return []
    return df["code"].astype(str).str.strip().tolist()


def _existing_codes() -> set[str]:
    df = read_daily_raw()
    if df.empty:
        return set()
    return set(df["code"].astype(str).str.strip().unique())


def _build_one(code: str, start: str, end: str) -> tuple[str, int, str | None]:
    """拉单只并落库。返回 ``(code, 行数, reason)``：reason 非 None 表示**网络失败**（可重试）。

    重试/退避由 ``fetch_hist`` 内部 ``_retry`` 兜底；东财对退市/早期停牌股常返回空，
    此时回退 Sina ``stock_zh_a_daily``（退市股修复幸存者偏差的关键路径）。空数据（行数 0、
    reason None）属正常，不计入失败清单。
    """
    try:
        df = fetch_hist(code, start=start, end=end, adjust="")
        if df.empty:
            df = fetch_delisted_daily(code, start=start, end=end)
        if not df.empty:
            write_daily_raw(df)
        return code, len(df), None
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] {code} 拉取失败: {e}", file=sys.stderr)
        return code, 0, str(e)[:200]


def _failed_file() -> Path:
    """失败 code 清单：``$QUANT_HOME/data/build_failed.jsonl``（JSONL，每行一个 entry）。"""
    from quant.store.paths import quant_home

    return quant_home() / "data" / "build_failed.jsonl"


def _read_failed() -> dict[str, dict]:
    p = _failed_file()
    if not p.is_file():
        return {}
    out: dict[str, dict] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and d.get("code") is not None:
            out[str(d["code"])] = d
    return out


def _write_failed(failed: dict[str, dict]) -> None:
    p = _failed_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(v, ensure_ascii=False) for v in failed.values()]
    p.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _refresh_adj_all(codes: list[str]) -> None:
    """对所有代码全量拉后复权因子并体检覆盖率（修复 build_daily 不写 adj_factor 的 P0）。

    旧版 ``_build_one`` 只写不复权 daily_raw，导致 ``load_adjusted_daily`` 在新库返回
    未复权价，除权日 raw close 假跳空（10 送 10 → 10 跌到 5）污染 mom/IC/ATR/回测。
    """
    from quant.data.adjust import refresh_adj_for_codes
    from quant.data.store import read_adj_factor, read_daily_raw

    try:
        n = refresh_adj_for_codes(codes)
        print(f"复权因子: {n} 条")
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] 复权因子全量拉取失败: {e}", file=sys.stderr)
        return
    try:
        adj_codes = set(read_adj_factor()["code"].astype(str).unique())
        raw_codes = set(read_daily_raw()["code"].astype(str).unique())
        cov = len(adj_codes & raw_codes) / max(len(raw_codes), 1)
        print(f"复权因子覆盖率: {cov:.1%} ({len(adj_codes & raw_codes)}/{len(raw_codes)})")
        if cov < 0.9:
            print(
                "[WARN] 复权因子覆盖率 <90%，因子/IC/回测可能用未复权价（除权日假跳空）",
                file=sys.stderr,
            )
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] 复权因子覆盖率检查失败: {e}", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-01-01", help="起始日 YYYY-MM-DD")
    ap.add_argument("--end", default=None, help="结束日 YYYY-MM-DD，默认今天")
    ap.add_argument("--workers", type=int, default=3, help="并发数（建议 3-5）")
    ap.add_argument("--limit", type=int, default=None, help="只拉前 N 只（调试用）")
    ap.add_argument("--codes", default=None, help="逗号分隔的代码列表（调试用）")
    ap.add_argument(
        "--ignore-existing",
        action="store_true",
        help="跳过代码去重，对所有代码拉（补日期缺口用；write 按 code+date 去重不重复）",
    )
    ap.add_argument(
        "--no-delisted",
        action="store_true",
        help="不并入退市股（默认并入，修复幸存者偏差）",
    )
    ap.add_argument(
        "--no-adj",
        action="store_true",
        help="跳过后复权因子全量初始化（默认拉取，修复除权日假跳空）",
    )
    ap.add_argument(
        "--retry-failed",
        action="store_true",
        help="只重试 build_failed.jsonl 里的失败 code（成功移除；失败 retries+1；达 --dead-threshold 标 dead 不再自动重试）",
    )
    ap.add_argument(
        "--dead-threshold",
        type=int,
        default=5,
        help="失败重试次数达此值标 dead（默认 5）",
    )
    ap.add_argument(
        "--req-interval",
        default=None,
        help="东财请求间隔秒（限速），格式 MIN,MAX 或 N（默认 1,3）。降频(如 3,6)避频控；升频慎用",
    )
    args = ap.parse_args()

    if args.req_interval:
        from common.utils.source_headers import set_eastmoney_interval
        parts = [p.strip() for p in args.req_interval.split(",") if p.strip()]
        if len(parts) == 1:
            lo = hi = int(parts[0])
        else:
            lo, hi = int(parts[0]), int(parts[1])
        set_eastmoney_interval(lo, hi)
        print(f"东财请求间隔: {lo},{hi}s")

    end = args.end or cn_now().strftime("%Y-%m-%d")

    if args.retry_failed:
        failed = _read_failed()
        todo = [c for c, e in failed.items() if not e.get("dead")]
        print(f"--retry-failed: 清单 {len(failed)} 只（dead {len(failed) - len(todo)}），重试 {len(todo)} 只")
        if not todo:
            print("无待重试 code，退出")
            return
        codes = list(todo)
    else:
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
            if not args.no_delisted:
                # 退市股并入（修复幸存者偏差）；失败则跳过，不阻断
                try:
                    del_codes = fetch_delisted_codes()["code"].astype(str).str.strip().tolist()
                    codes = list(dict.fromkeys(codes + del_codes))  # 去重保序
                    print(f"退市股并入: +{len(del_codes)} → 代码总数 {len(codes)}")
                except Exception as e:
                    print(f"[WARN] 退市清单拉取失败（跳过）: {e}", file=sys.stderr)
        if args.limit:
            codes = codes[: args.limit]
        print(f"全A 代码数: {len(codes)}")

        done = _existing_codes()
        if args.ignore_existing:
            todo = list(codes)
            print(f"--ignore-existing：强制全拉 {len(todo)} 只（write 按 code+date 去重，不重复）")
        else:
            todo = [c for c in codes if c not in done]
            print(f"已落库: {len(done)}，待拉: {len(todo)}")

    # 拉取（两种模式共用）。reason 非 None = 网络失败（可重试）；行数 0 且 reason None = 空数据（退市/停牌，正常）
    ok = empty = fail = 0
    results: dict[str, tuple[int, str | None]] = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_build_one, c, args.start, end): c for c in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            code, n, reason = fut.result()
            results[code] = (n, reason)
            if n > 0:
                ok += 1
            elif reason:
                fail += 1
            else:
                empty += 1
            if i % 50 == 0 or i == len(todo):
                print(f"进度 {i}/{len(todo)}  ok={ok} empty={empty} fail={fail}  {time.time()-t0:.0f}s")

    print(f"完成: ok={ok} empty={empty} fail={fail}  耗时 {time.time()-t0:.0f}s")

    # 失败清单：成功的移除；失败的 retries+1（达阈值标 dead）。空数据不动。
    failed = _read_failed()
    for code, (n, reason) in results.items():
        if n > 0:
            failed.pop(code, None)
        elif reason:
            retries = failed.get(code, {}).get("retries", 0) + 1
            failed[code] = {
                "code": code,
                "reason": reason,
                "ts": cn_now().isoformat(timespec="seconds"),
                "retries": retries,
                "dead": retries >= args.dead_threshold,
            }
    _write_failed(failed)
    n_dead = sum(1 for e in failed.values() if e.get("dead"))
    print(f"失败清单: {len(failed)} 只（dead {n_dead}）→ {_failed_file()}")

    # 后复权因子全量初始化（默认开；--no-adj 跳过）
    if not args.no_adj:
        _refresh_adj_all(codes)


if __name__ == "__main__":
    main()
