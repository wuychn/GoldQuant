"""补缺离线库历史段缺失列：float_mv/total_mv（精确历史市值）+ pre_close（推导）。

背景：build_daily 的 ``stock_zh_a_hist`` 行天生缺 ``float_mv/total_mv/name/pre_close``；
update_daily 的 spot 行齐全但只有当天。本脚本在合并后的统一库上跑一次，把历史段
``float_mv/total_mv`` 用 ``stock_value_em``（东财估值分析，逐日历史）**精确**补上，
``pre_close`` 用 ``close[t-1]`` 推导（同 code 相邻行）。

**name 刻意不灌历史**：当前名灌历史 = ST 过滤前视（当年 ST 现已摘帽的票会被当前名
误剔 / 当年健康现已 ST 的被漏入）。PIT 名靠 ``update_daily`` 的 ``name_snapshot``
从今天起逐日积累。确需当前名兜底时用 ``--fill-name``（理解前视取舍后使用）。

幂等 / 断点续传：
- 写回只补 NaN（已有 ``float_mv/total_mv/pre_close`` 不覆盖）。
- 拉网默认只请求「仍有任一行缺 float_mv 或 total_mv」的码；``--force`` 才全量重拉。
- **边拉边落盘**（默认每 ``--flush-every`` 只写一次）：中断后重跑只拉尚未落盘的缺码。

用法：
    poetry run python -m scripts.data.backfill_daily_meta --home ~/.quant
    poetry run python -m scripts.data.backfill_daily_meta --home ~/.quant --codes 000001,600519
    poetry run python -m scripts.data.backfill_daily_meta --home ~/.quant --workers 2 --req-interval 1,3
    poetry run python -m scripts.data.backfill_daily_meta --home ~/.quant --force
    poetry run python -m scripts.data.backfill_daily_meta --home ~/.quant --flush-every 20
    poetry run python -m scripts.data.backfill_daily_meta --home ~/.quant --fill-name
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from quant.data.schema import DAILY_RAW_COLUMNS
from quant.data.store import read_daily_raw, write_daily_raw
from quant.store.paths import override_quant_home

_MV_COLS = ("float_mv", "total_mv")
_PROGRESS_EVERY = 50  # 每完成 N 只打一行进度
_DEFAULT_FLUSH_EVERY = 50  # 每成功 N 只落盘一次（断点续传粒度）


def _log(msg: str) -> None:
    print(msg, flush=True)


def _log_err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def codes_needing_mv_fetch(
    daily: pd.DataFrame,
    codes: list[str],
    *,
    force: bool = False,
) -> list[str]:
    """选出需要打 ``stock_value_em`` 的码。

    默认：该码任一行缺 ``float_mv`` 或 ``total_mv``（列缺失视为全缺）才拉。
    ``force=True``：传入列表原样返回（全量重拉）。
    """
    if force:
        return list(codes)
    if not codes:
        return []
    want = set(codes)
    sub = daily[daily["code"].astype(str).str.strip().isin(want)].copy()
    if sub.empty:
        return list(codes)
    need: set[str] = set()
    for c in _MV_COLS:
        if c not in sub.columns:
            return list(codes)
        miss = sub[sub[c].isna() | (sub[c].astype(str) == "")]
        need.update(miss["code"].astype(str).str.strip().tolist())
    # 保持传入顺序
    return [c for c in codes if c in need]


def merge_mv_into_daily(
    daily: pd.DataFrame,
    mv_by_code: dict[str, dict[str, tuple[float, float]]],
) -> pd.DataFrame:
    """把市值 meta 填进 daily（只补 NaN，不覆盖已有值）。"""
    if not mv_by_code:
        return daily
    rows: list[tuple] = []
    for code, m in mv_by_code.items():
        for d, (fm, tm) in m.items():
            rows.append((code, d, fm, tm))
    if not rows:
        return daily
    meta = pd.DataFrame(rows, columns=["code", "date", *list(_MV_COLS)])
    out = daily.copy()
    out["code"] = out["code"].astype(str).str.strip()
    out["date"] = out["date"].astype(str)
    for c in _MV_COLS:
        if c not in out.columns:
            out[c] = pd.NA
    out = out.merge(meta, on=["code", "date"], how="left", suffixes=("", "_meta"))
    for c in _MV_COLS:
        out[c] = out[c].fillna(out[f"{c}_meta"])
    return out.drop(columns=[f"{c}_meta" for c in _MV_COLS])


def flush_mv_codes(
    home: Path,
    daily: pd.DataFrame,
    codes: list[str],
) -> None:
    """只写指定码的行到 daily_raw（分区内与旧数据去重 keep=last）。"""
    if not codes:
        return
    want = set(codes)
    subset = daily[daily["code"].astype(str).str.strip().isin(want)].reindex(columns=DAILY_RAW_COLUMNS)
    if subset.empty:
        return
    with override_quant_home(home):
        write_daily_raw(subset)


def _fetch_value_meta(
    code: str, *, interval: tuple[float, float]
) -> tuple[dict[str, tuple[float, float]] | None, str | None]:
    """拉单只历史市值 → ({date: (float_mv, total_mv)} | None, err|None)。"""
    import akshare as ak

    if interval[1] > 0:
        time.sleep(interval[0] + (interval[1] - interval[0]) * ((time.time() * 1000) % 1000) / 1000)
    try:
        df = ak.stock_value_em(symbol=str(code).strip())
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"
    if df is None or df.empty:
        return None, "空表"
    if "流通市值" not in df.columns:
        return None, f"缺列流通市值 cols={list(df.columns)[:8]}"
    out: dict[str, tuple[float, float]] = {}
    for _, r in df.iterrows():
        d = str(r.get("数据日期"))
        if len(d) == 10 and d[4] == "-":
            out[d] = (float(r.get("流通市值")), float(r.get("总市值")))
    if not out:
        return None, "无有效日期行"
    return out, None


def _fetch_code_names() -> dict[str, str]:
    """当前名表（仅 --fill-name 用；不灌历史，避免 ST 前视）。"""
    import akshare as ak

    try:
        df = ak.stock_info_a_code_name()
        return {
            str(r["code"]).strip(): str(r.get("name", "")).strip()
            for _, r in df.iterrows()
            if str(r.get("code", "")).strip() and str(r.get("name", "")).strip()
        }
    except Exception as e:  # noqa: BLE001
        _log_err(f"[WARN] stock_info_a_code_name 失败: {e}")
        return {}


def _derive_pre_close(daily: pd.DataFrame) -> pd.DataFrame:
    """同 code 相邻行推导 pre_close = close[t-1]；只补 NaN。"""
    if "pre_close" not in daily.columns:
        daily["pre_close"] = pd.NA
    c = pd.to_numeric(daily["close"], errors="coerce")
    prev = daily.assign(__c=c).groupby("code", sort=False)["__c"].shift(1)
    missing = daily["pre_close"].isna() | (daily["pre_close"].astype(str) == "")
    daily.loc[missing, "pre_close"] = prev.loc[missing]
    return daily


def _progress_line(*, i: int, total: int, ok: int, fail: int, flushed: int, t0: float) -> str:
    elapsed = max(time.time() - t0, 1e-6)
    rate = i / elapsed
    eta = (total - i) / rate if rate > 0 else 0.0
    return (
        f"  进度 {i}/{total} ({i / max(total, 1):.1%})  "
        f"ok={ok} fail={fail} flushed={flushed}  {elapsed:.0f}s  "
        f"{rate:.2f}码/s  ETA {eta:.0f}s"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True, help="quant-home 根（直接含 store/ 的那级）")
    ap.add_argument("--codes", default=None, help="逗号分隔代码；默认补库内全部")
    ap.add_argument("--workers", type=int, default=1, help="并发（默认 1；datacenter 接口较稳，可适度升）")
    ap.add_argument("--req-interval", default=None, help="请求间隔秒 MIN,MAX 或 N（默认 0,2）")
    ap.add_argument(
        "--force",
        action="store_true",
        help="强制对候选码全量重拉市值（默认只拉仍缺 float_mv/total_mv 的码）",
    )
    ap.add_argument(
        "--flush-every",
        type=int,
        default=_DEFAULT_FLUSH_EVERY,
        help=f"每成功拉取 N 只落盘一次（默认 {_DEFAULT_FLUSH_EVERY}；1=每只都写；越小续传越细、写盘越频）",
    )
    ap.add_argument("--fill-name", action="store_true", help="用当前名灌历史行（默认不灌，避免 ST 过滤前视）")
    args = ap.parse_args()

    flush_every = max(1, int(args.flush_every))

    # 尽早打日志，避免 patch/读库前干等无输出
    home = Path(args.home).expanduser()
    _log(f"[backfill] 开始 home={home}")

    from common.utils.source_headers import apply_source_header_patch

    apply_source_header_patch()

    if args.req_interval:
        parts = [p.strip() for p in args.req_interval.split(",") if p.strip()]
        interval = (float(parts[0]), float(parts[-1])) if parts else (0.0, 2.0)
    else:
        interval = (0.0, 2.0)

    _log("[backfill] 读取 daily_raw …")
    t_read = time.time()
    with override_quant_home(home):
        daily = read_daily_raw()
    if daily.empty:
        _log_err(f"[FATAL] {home} daily_raw 为空")
        sys.exit(1)
    _log(
        f"[backfill] daily_raw 就绪：{len(daily):,} 行 / "
        f"{daily['code'].nunique():,} 码  ({time.time() - t_read:.1f}s)"
    )

    daily["code"] = daily["code"].astype(str).str.strip()
    codes = list(daily["code"].unique())
    # 前缀过滤：与 build_daily/update_daily 一致
    from quant.config import load_quant_config

    prefixes = (
        (load_quant_config().get("gates") or {}).get("symbol_pool", {}).get("prefixes", ["60", "00", "30", "688"])
    )
    codes = [c for c in codes if c.startswith(tuple(prefixes))]
    if args.codes:
        want = {c.strip() for c in args.codes.split(",") if c.strip()}
        codes = [c for c in codes if c in want]
    n_total = len(codes)
    _log(f"[backfill] 候选码（前缀/指定后）: {n_total}")

    _log("[backfill] 扫描缺市值码 …")
    t_scan = time.time()
    codes_todo = codes_needing_mv_fetch(daily, codes, force=args.force)
    n_need = len(codes_todo)
    n_skip = n_total - n_need
    mode = "强制全量" if args.force else "仅缺市值"
    _log(
        f"[backfill] 计划 {mode}：总共 {n_total} 码 · "
        f"已齐跳过 {n_skip} · 待执行 {n_need}  "
        f"(扫描 {time.time() - t_scan:.1f}s；间隔 {interval[0]:.0f},{interval[1]:.0f}s；"
        f"workers={max(1, args.workers)}；flush_every={flush_every})"
    )
    if not codes_todo:
        _log("[backfill] 无待拉码，跳过市值请求；仍推导 pre_close 并写回")

    # 1) 逐只拉精确历史市值，边拉边落盘（支持断点续传）
    fail_codes: list[tuple[str, str]] = []
    t0 = time.time()
    done = fail = flushed = 0
    pending: dict[str, dict[str, tuple[float, float]]] = {}
    lock = threading.Lock()

    def _flush_pending(*, reason: str) -> None:
        nonlocal daily, pending, flushed
        if not pending:
            return
        batch = pending
        pending = {}
        codes_batch = list(batch.keys())
        _log(f"[backfill] 落盘 {len(codes_batch)} 码（{reason}）…")
        t_f = time.time()
        daily = merge_mv_into_daily(daily, batch)
        flush_mv_codes(home, daily, codes_batch)
        flushed += len(codes_batch)
        _log(f"[backfill] 落盘完成 +{len(codes_batch)} → 累计 flushed={flushed} ({time.time() - t_f:.1f}s)")

    if codes_todo:
        _log(f"[backfill] 开始拉取市值：0/{n_need}（边拉边落盘）")
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
            futs = {ex.submit(_fetch_value_meta, c, interval=interval): c for c in codes_todo}
            for i, fut in enumerate(as_completed(futs), 1):
                code = futs[fut]
                try:
                    mv, err = fut.result()
                except Exception as e:  # noqa: BLE001
                    mv, err = None, f"{type(e).__name__}: {e}"
                if mv:
                    with lock:
                        pending[code] = mv
                        done += 1
                        if len(pending) >= flush_every:
                            _flush_pending(reason=f"满 {flush_every}")
                else:
                    fail += 1
                    reason = err or "未知"
                    fail_codes.append((code, reason))
                    _log_err(f"  [WARN] {code} 失败: {reason}")
                if i == 1 or i % _PROGRESS_EVERY == 0 or i == n_need:
                    _log(
                        _progress_line(
                            i=i, total=n_need, ok=done, fail=fail, flushed=flushed, t0=t0
                        )
                    )
        with lock:
            _flush_pending(reason="收尾")
        _log(f"[backfill] 市值拉取结束：ok={done} fail={fail} flushed={flushed} 耗时 {time.time() - t0:.0f}s")
        if fail_codes:
            sample = ", ".join(f"{c}({r})" for c, r in fail_codes[:10])
            more = f" …另有 {len(fail_codes) - 10} 只" if len(fail_codes) > 10 else ""
            _log_err(f"[backfill] 失败样例: {sample}{more}（可重跑本脚本只补缺）")

    # 2) 推导 pre_close（本地，整表一次）
    _log("[backfill] 推导 pre_close …")
    t_pc = time.time()
    daily = _derive_pre_close(daily)
    _log(f"[backfill] pre_close 完成 ({time.time() - t_pc:.1f}s)")

    # 3) 可选当前名（默认不灌历史）
    if args.fill_name:
        _log("[backfill] --fill-name：拉取当前名表 …")
        names = _fetch_code_names()
        daily["name"] = daily["code"].map(names).fillna(daily.get("name", ""))
        _log(f"[backfill] name 填充：映射 {len(names)} 只")

    # 4) 最终写回（确保 pre_close / name 落盘；市值已在批次中写入）
    _log("[backfill] 最终写回 daily_raw（含 pre_close）…")
    t_w = time.time()
    daily = daily.reindex(columns=DAILY_RAW_COLUMNS)
    with override_quant_home(home):
        write_daily_raw(daily)
    _log(f"[backfill] 最终写回完成 ({time.time() - t_w:.1f}s)")

    # 覆盖报告
    n = len(daily)
    mv = daily["float_mv"].notna().sum()
    pc = daily["pre_close"].notna().sum()
    _log(
        f"[backfill] 汇总：{n:,} 行 · float_mv 非空 {mv:,}（{mv / max(n, 1):.1%}）· "
        f"pre_close 非空 {pc:,}（{pc / max(n, 1):.1%}）"
    )
    _log(
        f"[backfill] 拉取统计：计划 {n_need} · 成功 {done} · 失败 {fail} · "
        f"跳过已齐 {n_skip} · 落盘批次累计 {flushed}"
    )
    _log(f"[backfill] 建议校验：python -m scripts.data.validate_library --home {home}")


if __name__ == "__main__":
    main()
