"""从 daily_raw 剔除无效盘口行（close<=0 / OHLC 不自洽等）。

``update_daily`` 已在入口拒写；本脚本清理**历史已入库**脏行。按年分区整表重写
（非 append），默认 dry-run。

用法：
    poetry run python -m scripts.data.scrub_invalid_bars --home ~/.quant --dry-run
    poetry run python -m scripts.data.scrub_invalid_bars --home ~/.quant --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from quant.data.schema import DAILY_RAW_COLUMNS
from quant.store.paths import override_quant_home


def invalid_bar_mask(daily: pd.DataFrame) -> pd.Series:
    """与 validate / filter_valid_spot_bars 对齐的脏行掩码。"""
    d = daily
    close = pd.to_numeric(d["close"], errors="coerce") if "close" in d.columns else pd.Series(0.0, index=d.index)
    open_ = pd.to_numeric(d["open"], errors="coerce") if "open" in d.columns else close
    high = pd.to_numeric(d["high"], errors="coerce") if "high" in d.columns else close
    low = pd.to_numeric(d["low"], errors="coerce") if "low" in d.columns else close
    bad = close.isna() | (close <= 0)
    bad |= high.isna() | low.isna() | open_.isna()
    bad |= high < low
    bad |= high < pd.concat([open_, close], axis=1).max(axis=1)
    bad |= low > pd.concat([open_, close], axis=1).min(axis=1)
    if "volume" in d.columns:
        vol = pd.to_numeric(d["volume"], errors="coerce")
        bad |= vol.notna() & (vol < 0)
    if "amount" in d.columns:
        amt = pd.to_numeric(d["amount"], errors="coerce")
        bad |= amt.notna() & (amt < 0)
    return bad.fillna(True)


def _rewrite_year_partitions(daily: pd.DataFrame) -> int:
    """按年覆盖写回 daily_raw 分区，返回写入年数。"""
    from quant.data.store import _file_lock, _write_parquet_atomic, daily_raw_dir

    if daily.empty:
        return 0
    d = daily.reindex(columns=[c for c in DAILY_RAW_COLUMNS if c in daily.columns])
    root = daily_raw_dir()
    n_years = 0
    for year, g in d.groupby(d["date"].astype(str).str[:4]):
        path = root / f"year={year}" / "part.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        g = g.sort_values(["code", "date"]).reset_index(drop=True)
        with _file_lock(path):
            _write_parquet_atomic(path, g)
        n_years += 1
    return n_years


def main() -> None:
    ap = argparse.ArgumentParser(description="剔除 daily_raw 无效盘口行")
    ap.add_argument("--home", required=True, help="quant-home 根（含 store/）")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写（默认）")
    ap.add_argument("--apply", action="store_true", help="真正按年分区重写")
    args = ap.parse_args()
    if args.apply and args.dry_run:
        print("不能同时 --apply 与 --dry-run", file=sys.stderr)
        sys.exit(2)
    apply = bool(args.apply)
    if not apply:
        args.dry_run = True

    home = Path(args.home).expanduser()
    print(f"[scrub] home={home} mode={'APPLY' if apply else 'DRY-RUN'}", flush=True)

    from quant.data.store import read_daily_raw

    with override_quant_home(home):
        daily = read_daily_raw()
        if daily.empty:
            print("[scrub] daily_raw 为空，退出")
            return
        bad = invalid_bar_mask(daily)
        n_bad = int(bad.sum())
        print(f"[scrub] 总行 {len(daily):,} · 无效 {n_bad:,}")
        if n_bad:
            sample = daily.loc[bad, [c for c in ("code", "date", "open", "high", "low", "close", "volume") if c in daily.columns]]
            print(sample.head(20).to_string())
            if n_bad > 20:
                print(f"  …另有 {n_bad - 20} 行")
        if not apply:
            print("[scrub] dry-run 结束；确认后加 --apply 写回")
            return
        if n_bad == 0:
            print("[scrub] 无需写回")
            return
        clean = daily.loc[~bad].copy()
        # 被删光的年份也要写空？保留原分区仅含 clean 年份；若某年全脏则写空表覆盖
        years_before = set(daily["date"].astype(str).str[:4].unique())
        years_after = set(clean["date"].astype(str).str[:4].unique()) if not clean.empty else set()
        n_years = _rewrite_year_partitions(clean)
        # 全年被删：写空分区
        from quant.data.store import _file_lock, _write_parquet_atomic, daily_raw_dir

        for y in sorted(years_before - years_after):
            path = daily_raw_dir() / f"year={y}" / "part.parquet"
            empty = clean.iloc[0:0].reindex(columns=[c for c in DAILY_RAW_COLUMNS if c in daily.columns])
            path.parent.mkdir(parents=True, exist_ok=True)
            with _file_lock(path):
                _write_parquet_atomic(path, empty)
            n_years += 1
        print(f"[scrub] 已写回 {n_years} 个年分区 · 保留 {len(clean):,} 行 · 删除 {n_bad:,}")


if __name__ == "__main__":
    main()
