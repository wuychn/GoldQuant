"""合并离线库（build_daily 到昨日）与每日增量（update_daily 今日起）为统一库。

场景：全量离线 build 需数小时/数天，期间不想让它与日常 update_daily 抢同一 store。
方案：build_daily ``--end <昨日>`` 写离线 home；update_daily 每日写增量 home；
build 完成后用本脚本合并到输出 home。

三个目录均为 **quant-home 根**（含 ``store/`` 布局，即 ``quant_home()/store``）。

一致性保证：
- **daily_raw 归一到 ``DAILY_RAW_COLUMNS``（13 列）**：build 的 ``stock_zh_a_hist`` 行缺
  name/pre_close/float_mv/total_mv，update 的 spot 行齐全——统一 schema 后同 code+date 去重，
  update 覆盖 build。
- **复权不复发除权尖刺 bug**：合并只做 raw + adj_factor 的并集去重，**不自己算复权**；
  读时经 ``quant/data/adjust.py:apply_hfq``（merge_asof 还原累积因子）得到连续后复权价。
  合并不改任何复权逻辑，故不会重现"精确 join 只在除权日放大因子"的旧 bug。
- **index_daily / calendar / snapshot 目录取并集**（snapshot 冲突 daily 优先）。

用法：
    poetry run python -m scripts.data.merge_library \\
        --offline <离线home> --daily <增量home> --out <输出home>

    # 例（用 QUANT_HOME 区分三处）：
    QUANT_HOME=~/.quant/offline poetry run python -m scripts.data.build_daily --start 2021-01-01 --end 2026-08-02
    QUANT_HOME=~/.quant/daily   poetry run python -m scripts.data.update_daily
    poetry run python -m scripts.data.merge_library --offline ~/.quant/offline --daily ~/.quant/daily --out ~/.quant
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import pandas as pd

from common.progress_log import log_progress, log_progress_done, log_progress_error, log_progress_start
from quant.data.schema import ADJ_FACTOR_COLUMNS, DAILY_RAW_COLUMNS
from quant.data.store import (
    read_adj_factor,
    read_calendar,
    read_daily_raw,
    write_adj_factor,
    write_calendar,
    write_daily_raw,
    write_index_daily,
)
from quant.store.paths import override_quant_home, quant_home

_SCOPE = "merge_library"

# 这四个由专门的合并逻辑处理；其余 store 下产物（universe/industry/name_snapshot/
# fundamental_pit/fund_flow/hot_rank/theme_mom/listing_dates...）走目录并集复制。
_MERGED_ARTIFACTS = ("daily_raw/", "adj_factor/", "index_daily/", "calendar.parquet")


def _read_from(home: Path, fn, **kw):
    """在指定 home 下调用 store 读函数。"""
    with override_quant_home(home):
        return fn(**kw)


def _merge_daily_raw(offline: Path, daily: Path, out: Path) -> int:
    frames: list[pd.DataFrame] = []
    for home in (offline, daily):
        df = _read_from(home, read_daily_raw)
        if df is None or df.empty:
            continue
        # 归一到统一 13 列：build 行缺的 name/pre_close/float_mv/total_mv 补 NaN
        frames.append(df.reindex(columns=DAILY_RAW_COLUMNS))
    if not frames:
        print("[merge] 两库均无 daily_raw，跳过")
        return 0
    merged = pd.concat(frames, ignore_index=True)
    merged = merged.drop_duplicates(subset=["code", "date"], keep="last")  # update 覆盖 build
    merged = merged.sort_values(["code", "date"]).reset_index(drop=True)
    with override_quant_home(out):
        write_daily_raw(merged)
    print(f"[merge] daily_raw: {len(merged)} 行 / {merged['code'].nunique()} 码")
    return len(merged)


def _merge_adj(offline: Path, daily: Path, out: Path) -> int:
    frames: list[pd.DataFrame] = []
    for home in (offline, daily):
        df = _read_from(home, read_adj_factor)
        if df is None or df.empty:
            continue
        frames.append(df.reindex(columns=ADJ_FACTOR_COLUMNS))
    if not frames:
        print("[merge] 两库均无 adj_factor，跳过")
        return 0
    merged = pd.concat(frames, ignore_index=True)
    merged = merged.drop_duplicates(subset=["code", "date"], keep="last")
    with override_quant_home(out):
        write_adj_factor(merged)
    print(f"[merge] adj_factor: {len(merged)} 行 / {merged['code'].nunique()} 码")
    return len(merged)


def _merge_index(offline: Path, daily: Path, out: Path) -> int:
    import pyarrow.parquet as pq

    frames: list[pd.DataFrame] = []
    for home in (offline, daily):
        p = home / "store" / "index_daily" / "part.parquet"
        if not p.is_file():
            continue
        try:
            frames.append(pq.read_table(p).to_pandas())
        except Exception as e:  # noqa: BLE001
            print(f"[merge][WARN] 读 {p} 失败: {e}", file=sys.stderr)
    if not frames:
        print("[merge] 两库均无 index_daily，跳过")
        return 0
    merged = pd.concat(frames, ignore_index=True)
    merged = merged.drop_duplicates(subset=["code", "date"], keep="last")
    with override_quant_home(out):
        write_index_daily(merged)
    print(f"[merge] index_daily: {len(merged)} 行")
    return len(merged)


def _merge_calendar(offline: Path, daily: Path, out: Path) -> int:
    days: set[str] = set()
    for home in (offline, daily):
        days |= set(_read_from(home, read_calendar))
    if not days:
        print("[merge] 两库均无 calendar，跳过")
        return 0
    with override_quant_home(out):
        write_calendar(sorted(days))
    print(f"[merge] calendar: {len(days)} 天")
    return len(days)


def _copy_snapshots(offline: Path, daily: Path, out: Path) -> int:
    """复制其余 store 产物（快照/上市日表等），并集、daily 优先。"""
    merged: dict[str, Path] = {}
    for home in (offline, daily):
        src = home / "store"
        if not src.is_dir():
            continue
        for p in src.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(src).as_posix()
            if rel.startswith(_MERGED_ARTIFACTS):
                continue
            merged[rel] = p  # 后者（daily）覆盖前者（offline）
    if not merged:
        print("[merge] 无 snapshot 产物需复制")
        return 0
    out_store = out / "store"
    for rel, p in merged.items():
        dst = out_store / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)
    print(f"[merge] snapshot 文件复制: {len(merged)} 个")
    return len(merged)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", required=True, help="离线库 quant-home 根目录（含 store/；build_daily --end 昨日写入处）")
    ap.add_argument("--daily", required=True, help="增量库 quant-home 根目录（含 store/；update_daily 每日写入处）")
    ap.add_argument("--out", required=True, help="合并输出 quant-home 根目录（含 store/；合并后的统一库）")
    args = ap.parse_args()

    offline, daily, out = Path(args.offline).expanduser(), Path(args.daily).expanduser(), Path(args.out).expanduser()
    log_progress_start(
        _SCOPE,
        "开始",
        detail=f"offline={offline} daily={daily} out={out}",
    )
    for name, p in (("offline", offline), ("daily", daily)):
        if not (p / "store").is_dir():
            log_progress_error(_SCOPE, "失败", detail=f"{name} 不是有效 quant-home（缺 store/）: {p}")
            sys.exit(1)
    try:
        out.mkdir(parents=True, exist_ok=True)
        log_progress(_SCOPE, "合并 daily_raw …")
        _merge_daily_raw(offline, daily, out)
        log_progress(_SCOPE, "合并 adj_factor …")
        _merge_adj(offline, daily, out)
        log_progress(_SCOPE, "合并 index_daily …")
        _merge_index(offline, daily, out)
        log_progress(_SCOPE, "合并 calendar …")
        _merge_calendar(offline, daily, out)
        log_progress(_SCOPE, "复制 snapshot …")
        _copy_snapshots(offline, daily, out)
        log_progress_done(
            _SCOPE,
            "成功",
            detail=f"out={out}；建议 validate_library --home {out}",
        )
    except Exception as e:
        log_progress_error(_SCOPE, "失败", detail=f"{type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    main()
