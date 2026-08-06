"""全量初始化离线日线库：逐只拉 stock_zh_a_hist（不复权）+ 指数 + 交易日历。

默认智能断点续传：
  1. 逐只完整性检查（相对 --start/--end 交易日历，缺一天即补）；完整则跳过
  2. 待拉码按**完整检查区间**补拉（与 CLI 一致，write 去重）；停牌日写入无行情豁免
  3. 自动并入 build_failed.jsonl 中非 dead 失败码
  4. 再扫市场级缺失交易日，有缺口则对缺口窗全代码补拉

失败重试/退避由 fetch_hist 内部 ``_retry`` 兜底。建议夜间 + 低并发。

用法：
    poetry run python -m scripts.data.build_daily --start 2021-01-01 --workers 1 --req-interval 5,10
    poetry run python -m scripts.data.build_daily --start 2021-01-01 --end 2026-07-25
    # 强制全拉一段（跳过完整性检查与市场缺口第二轮）：
    poetry run python -m scripts.data.build_daily --start 2026-07-20 --end 2026-07-25 --ignore-existing
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from quant.data.delist import fetch_delisted_codes, fetch_delisted_daily
from quant.data.fetch import fetch_hist, fetch_index, fetch_trade_calendar
from quant.data.store import (
    read_calendar,
    read_daily_raw,
    write_calendar,
    write_daily_raw,
    write_index_daily,
)
from common.timeutil import cn_now


def last_cal_day_on_or_before(calendar: list[str], end: str) -> str | None:
    """返回日历中 ≤ end 的最近交易日；无则 None。"""
    candidates = [d for d in calendar if d <= end]
    return candidates[-1] if candidates else None


def _no_bar_path() -> Path:
    from quant.store.paths import quant_home

    return quant_home() / "data" / "no_bar_dates.json"


def load_no_bar_map() -> dict[str, set[str]]:
    """已确认无 K 线的 (code → dates)，停牌日等豁免完整性检查。"""
    p = _no_bar_path()
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, set[str]] = {}
    for k, v in raw.items():
        if isinstance(v, list):
            out[str(k)] = {str(d) for d in v}
    return out


def save_no_bar_map(mp: dict[str, set[str]]) -> None:
    p = _no_bar_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {c: sorted(dates) for c, dates in sorted(mp.items()) if dates}
    with _no_bar_lock():
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")


def _no_bar_lock():
    """no_bar_dates.json 写锁（多线程 RMW 安全，复用 filelock）。"""
    from contextlib import nullcontext

    try:
        from filelock import FileLock
    except ImportError:
        return nullcontext()
    p = _no_bar_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(str(p) + ".lock", timeout=120)


def add_no_bar_dates(code: str, dates: set[str]) -> int:
    """追加无行情豁免日，返回新增条数。"""
    if not dates:
        return 0
    c = str(code).strip()
    add = {str(d) for d in dates}
    with _no_bar_lock():
        mp = load_no_bar_map()
        before = set(mp.get(c, set()))
        merged = before | add
        mp[c] = merged
        _save_no_bar_map_unlocked(mp)
    return len(merged - before)


def _save_no_bar_map_unlocked(mp: dict[str, set[str]]) -> None:
    """已持锁时调用，避免重入。"""
    p = _no_bar_path()
    payload = {c: sorted(dates) for c, dates in sorted(mp.items()) if dates}
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")


def incomplete_fetch_plans(
    codes: list[str],
    *,
    start: str,
    end: str,
    daily: pd.DataFrame,
    calendar: list[str],
    listing_map: dict[str, str] | None = None,
    no_bar_map: dict[str, set[str]] | None = None,
) -> list[tuple[str, str, str]]:
    """逐只完整性检查，返回 ``[(code, fetch_start, fetch_end), ...]``（保序）。

    期望交易日 = 日历 ∩ [start, end] − 无行情豁免；有上市日则从
    ``max(start, listing_date)`` 起算。缺任意一天则补拉，窗口为**完整检查区间**
    ``[start, end]``（与 CLI ``--start/--end`` 一致，write 按 code+date 去重）。
    """
    if not codes:
        return []
    expected_all = {d for d in calendar if start <= d <= end}
    listing = listing_map or {}
    no_bar = no_bar_map if no_bar_map is not None else load_no_bar_map()

    # 库内每只票的实际首日（上市日 fallback）：listing_map 有则用 listing_map，
    # 没有则用 daily_raw 中的首条日期（新股上市前的日期不该当"缺日"）。
    first_date_by_code: dict[str, str] = {}
    if daily is not None and not daily.empty:
        for c, g in daily.groupby(daily["code"].astype(str).str.strip()):
            first_date_by_code[c] = str(g["date"].min())

    def _lo(code: str) -> str:
        # 优先 listing_map（精确 IPO 日），其次库首日（上市后的首条行情日）
        ld = listing.get(code)
        if ld and ld > start:
            return ld
        fd = first_date_by_code.get(code)
        # 库首日晚于 start 超过 7 个日历日 → 大概率新股（无 listing_map 时），
        # 从库首日起算期望日，避免把上市前当"缺日"。小偏差（停牌/偶发缺日）仍按 start。
        if fd and fd > start:
            from datetime import date, timedelta

            try:
                delta = (date.fromisoformat(fd[:10]) - date.fromisoformat(start[:10])).days
            except ValueError:
                delta = 0
            if delta > 7:
                return fd
        return start

    if not expected_all:
        if daily is None or daily.empty:
            return [(str(c).strip(), start, end) for c in codes]
        have_codes = set(daily["code"].astype(str).str.strip().unique())
        return [
            (str(c).strip(), start, end)
            for c in codes
            if str(c).strip() not in have_codes
        ]

    have_by_code: dict[str, set[str]] = {}
    if daily is not None and not daily.empty:
        g = daily.copy()
        g["code"] = g["code"].astype(str).str.strip()
        g["date"] = g["date"].astype(str)
        g = g[(g["date"] >= start) & (g["date"] <= end)]
        if not g.empty:
            have_by_code = {
                str(code): set(grp["date"].tolist())
                for code, grp in g.groupby("code", sort=False)
            }

    out: list[tuple[str, str, str]] = []
    for code in codes:
        c = str(code).strip()
        lo = _lo(c)
        expected = {d for d in expected_all if d >= lo} - no_bar.get(c, set())
        if not expected:
            continue
        have = have_by_code.get(c, set())
        missing = expected - have
        if not missing:
            continue
        # 补拉全检查区间，避免日志/语义上出现「只拉了某几天」的误解
        out.append((c, start, end))
    return out


def incomplete_codes(
    codes: list[str],
    *,
    start: str,
    end: str,
    daily: pd.DataFrame,
    calendar: list[str],
    listing_map: dict[str, str] | None = None,
    no_bar_map: dict[str, set[str]] | None = None,
) -> list[str]:
    """``incomplete_fetch_plans`` 的代码列表视图（兼容单测 / 调用方）。"""
    return [
        c
        for c, _, _ in incomplete_fetch_plans(
            codes,
            start=start,
            end=end,
            daily=daily,
            calendar=calendar,
            listing_map=listing_map,
            no_bar_map=no_bar_map,
        )
    ]


def market_missing_dates(*, start: str, end: str) -> list[str]:
    """``[start, end]`` 内日历有、但 daily_raw 全市场都没有的交易日。"""
    from scripts.data.maintain import scan_missing_dates

    return [d for d in scan_missing_dates(end) if d >= start]


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


def _probe_start(start: str, *, lookback_days: int = 90) -> str:
    """目标窗起点往前推若干自然日，作宽窗探测。"""
    from datetime import date, timedelta

    d0 = date.fromisoformat(str(start)[:10])
    return (d0 - timedelta(days=lookback_days)).isoformat()


def _mark_holes_as_no_bar(code: str, *, start: str, end: str, returned_dates: set[str]) -> int:
    """成功拉到数据后：在返回区间内、日历有但源站未返回的交易日记为无行情豁免。"""
    if not returned_dates:
        return 0
    cal = read_calendar()
    lo, hi = min(returned_dates), max(returned_dates)
    # 仅标记「有返回覆盖的跨度」内部空洞，避免限流截断时误伤首尾
    span_lo = max(start, lo)
    span_hi = min(end, hi)
    holes = {d for d in cal if span_lo <= d <= span_hi} - returned_dates
    n = add_no_bar_dates(code, holes)
    if n:
        print(
            f"[INFO] {code} 检查区间 {start}~{end} 内记入无行情豁免 {n} 天"
            f"（源站未返回，多为停牌）",
            file=sys.stderr,
        )
    return n


def _build_one(code: str, start: str, end: str) -> tuple[str, int, str | None]:
    """拉单只并落库。返回 ``(code, 行数, reason)``：reason 非 None 表示**失败可重试**。

    ``start/end`` 为完整性检查全区间（与 CLI ``--start/--end`` 一致）。
    双源皆空时宽窗探测区分：假空写入 / 确认无行情并豁免 / 未确认则 WARN 记失败。
    """
    try:
        df = fetch_hist(code, start=start, end=end, adjust="")
        if df.empty:
            df = fetch_delisted_daily(code, start=start, end=end)
        if not df.empty:
            write_daily_raw(df)
            ret = set(df["date"].astype(str))
            _mark_holes_as_no_bar(code, start=start, end=end, returned_dates=ret)
            return code, len(df), None

        # —— 全区间双源空：宽窗探测 ——
        p_start = _probe_start(start)
        probe = fetch_hist(code, start=p_start, end=end, adjust="")
        if probe.empty:
            probe = fetch_delisted_daily(code, start=p_start, end=end)

        if not probe.empty:
            overlap = probe[(probe["date"] >= start) & (probe["date"] <= end)]
            if not overlap.empty:
                write_daily_raw(overlap)
                ret = set(overlap["date"].astype(str))
                _mark_holes_as_no_bar(code, start=start, end=end, returned_dates=ret)
                print(
                    f"[INFO] {code} 检查区间 {start}~{end} 首次空，"
                    f"宽窗探测命中 {len(overlap)} 行已写入（疑假空/限流）",
                    file=sys.stderr,
                )
                return code, len(overlap), None
            # 宽窗有数、检查区间内无 → 整段检查窗记豁免
            cal = read_calendar()
            exempt = {d for d in cal if start <= d <= end}
            n = add_no_bar_dates(code, exempt)
            print(
                f"[INFO] {code} 确认无行情: 检查区间 {start}~{end} 无K线，"
                f"宽窗 {p_start}~{end} 有 {len(probe)} 行；"
                f"已豁免 {n} 个交易日（停牌/未上市）",
                file=sys.stderr,
            )
            return code, 0, None

        # 四个 API 调用全部成功返回空（非异常）→ 确认无行情（新股/未上市/退市）
        cal = read_calendar()
        exempt = {d for d in cal if start <= d <= end}
        n = add_no_bar_dates(code, exempt)
        print(
            f"[INFO] {code} 确认无行情（新股/未上市/退市），"
            f"检查区间 {start}~{end} 已豁免 {n} 天",
            file=sys.stderr,
        )
        return code, 0, None
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] {code} 拉取失败: {type(e).__name__}: {e}", file=sys.stderr)
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


def _update_failed(
    results: dict[str, tuple[int, str | None]],
    *,
    dead_threshold: int,
) -> dict[str, dict]:
    """已解决（成功 n>0 或 确认无行情 n=0/reason=None）→ 移除；网络失败（reason 非空）→ retries+1（达阈值标 dead）。

    ``_build_one`` 返回 ``(0, None)`` 的唯一路径是宽窗探测确认的「检查区间无行情」
    （line 277，probe 命中窗外数据）——这是**已解决**状态，不是未知空数据；故 reason
    为空一律移除。修前对 ``(0, None)`` 不动，导致曾失败的退市码永留 failed、
    ``--retry-failed`` 反复重拉已确认无行情的码。
    """
    failed = _read_failed()
    for code, (n, reason) in results.items():
        if reason:
            retries = failed.get(code, {}).get("retries", 0) + 1
            failed[code] = {
                "code": code,
                "reason": reason,
                "ts": cn_now().isoformat(timespec="seconds"),
                "retries": retries,
                "dead": retries >= dead_threshold,
            }
        else:
            failed.pop(code, None)
    _write_failed(failed)
    return failed


def _run_pull(
    plans: list[tuple[str, str, str]],
    *,
    workers: int,
    label: str = "",
) -> dict[str, tuple[int, str | None]]:
    """并发拉取 ``plans``（每项 ``(code, fetch_start, fetch_end)``）；返回 ``{code: (n, reason)}``。"""
    if not plans:
        print(f"{label}跳过：待拉 0 只")
        return {}
    prefix = f"{label}" if label else ""
    ok = empty = fail = 0
    results: dict[str, tuple[int, str | None]] = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_build_one, c, fs, fe): c for c, fs, fe in plans}
        for i, fut in enumerate(as_completed(futs), 1):
            code, n, reason = fut.result()
            results[code] = (n, reason)
            if n > 0:
                ok += 1
            elif reason:
                fail += 1
            else:
                empty += 1
            if i % 50 == 0 or i == len(plans):
                print(
                    f"{prefix}进度 {i}/{len(plans)}  "
                    f"ok={ok} empty={empty} fail={fail}  {time.time() - t0:.0f}s"
                )
    print(f"{prefix}完成: ok={ok} empty={empty} fail={fail}  耗时 {time.time() - t0:.0f}s")
    return results


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
        help="跳过完整性检查，对所有代码拉（补日期缺口用；write 按 code+date 去重不重复）；并跳过市场缺口第二轮",
    )
    ap.add_argument(
        "--no-gap-fill",
        action="store_true",
        help="只做代码级续传，不做市场级缺失交易日第二轮补拉",
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
    do_gap_fill = False
    plans: list[tuple[str, str, str]] = []

    if args.retry_failed:
        failed = _read_failed()
        todo = [c for c, e in failed.items() if not e.get("dead")]
        print(
            f"--retry-failed: 清单 {len(failed)} 只（dead {len(failed) - len(todo)}），"
            f"重试 {len(todo)} 只"
        )
        if not todo:
            print("无待重试 code，退出")
            return
        codes = list(todo)
        plans = [(c, args.start, end) for c in todo]
    else:
        # 交易日历
        try:
            cal = fetch_trade_calendar()
            write_calendar([d for d in cal if d <= end])
            print(f"交易日历: {len(cal)} 条")
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] 交易日历拉取失败: {e}", file=sys.stderr)

        # 基准指数
        for idx in ("000300", "000905", "000852"):
            try:
                df = fetch_index(idx, start=args.start, end=end)
                write_index_daily(df)
                print(f"指数 {idx}: {len(df)} 行")
            except Exception as e:  # noqa: BLE001
                print(f"[WARN] 指数 {idx} 失败: {e}", file=sys.stderr)

        # 代码表
        if args.codes:
            codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        else:
            try:
                codes = _all_codes()
            except Exception as e:  # noqa: BLE001
                print(f"[FATAL] 无法获取代码表（spot_em 失败）: {e}", file=sys.stderr)
                sys.exit(1)
            if not args.no_delisted:
                try:
                    del_codes = fetch_delisted_codes()["code"].astype(str).str.strip().tolist()
                    codes = list(dict.fromkeys(codes + del_codes))
                    print(f"退市股并入: +{len(del_codes)} → 代码总数 {len(codes)}")
                except Exception as e:  # noqa: BLE001
                    print(f"[WARN] 退市清单拉取失败（跳过）: {e}", file=sys.stderr)
        if args.limit:
            codes = codes[: args.limit]
        print(f"全A 代码数: {len(codes)}")

        if args.ignore_existing:
            plans = [(c, args.start, end) for c in codes]
            print(
                f"--ignore-existing：强制全拉 {len(plans)} 只 "
                f"{args.start}~{end}（write 按 code+date 去重，不重复）"
            )
        else:
            daily = read_daily_raw(start=args.start, end=end)
            cal_local = read_calendar()
            listing_map: dict[str, str] = {}
            try:
                from quant.data.listing import read_listing_map

                listing_map = read_listing_map()
            except Exception as e:  # noqa: BLE001
                print(f"[WARN] 读取 listing_dates 失败（按 start 起算）: {e}", file=sys.stderr)
            plans = incomplete_fetch_plans(
                codes,
                start=args.start,
                end=end,
                daily=daily,
                calendar=cal_local,
                listing_map=listing_map,
                no_bar_map=load_no_bar_map(),
            )
            n_have = 0 if daily.empty else daily["code"].astype(str).nunique()
            print(f"完整性检查区间: {args.start} ~ {end}")
            print(
                f"完整性检查: 区间内已有 {n_have} 只，"
                f"缺日待拉 {len(plans)}/{len(codes)}（待拉码按全区间 {args.start}~{end} 补拉）"
            )
            # 自动并入非 dead 失败码（尚无计划的用全区间；已有计划保留缺失窗）
            failed_map = _read_failed()
            planned = {c for c, _, _ in plans}
            extra_all = [
                c for c, e in failed_map.items() if not e.get("dead") and c not in planned
            ]
            if args.codes or args.limit:
                code_set = set(codes)
                extra = [c for c in extra_all if c in code_set]
            else:
                extra = extra_all
            if extra:
                plans.extend((c, args.start, end) for c in extra)
                print(f"并入失败清单非 dead: +{len(extra)} → 待拉 {len(plans)}")
            do_gap_fill = not args.no_gap_fill

    # 第一轮拉取
    results = _run_pull(plans, workers=args.workers, label="[轮1] ")
    failed = _update_failed(results, dead_threshold=args.dead_threshold)
    n_dead = sum(1 for e in failed.values() if e.get("dead"))
    print(f"失败清单: {len(failed)} 只（dead {n_dead}）→ {_failed_file()}")

    # 第二轮：市场级缺失交易日窗口补拉
    if do_gap_fill:
        try:
            missing = market_missing_dates(start=args.start, end=end)
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] 市场缺口扫描失败: {e}", file=sys.stderr)
            missing = []
        if missing:
            gap_start, gap_end = missing[0], missing[-1]
            print(
                f"[轮2] 市场级缺口 {len(missing)} 个交易日: "
                f"{gap_start} ~ {gap_end} → 全代码补拉该窗"
            )
            plans2 = [(c, gap_start, gap_end) for c in codes]
            results2 = _run_pull(plans2, workers=args.workers, label="[轮2] ")
            failed = _update_failed(results2, dead_threshold=args.dead_threshold)
            n_dead = sum(1 for e in failed.values() if e.get("dead"))
            print(f"失败清单: {len(failed)} 只（dead {n_dead}）→ {_failed_file()}")
        else:
            print("[轮2] 市场级日期完整，无缺口")

    # 后复权因子全量初始化（默认开；--no-adj 跳过）
    if not args.no_adj:
        _refresh_adj_all(codes)


if __name__ == "__main__":
    main()
