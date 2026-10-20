"""把 daily_raw 成交量统一为股，并补 pre_close / 换手率。

东财 hist/spot 成交量是「手」，新浪 hist 已是「股」。按
``amount/(volume×close)`` 判别后把手数行乘 100。幂等，可安全重跑。

不灌 name（避免用当前名污染 ST 历史，前视）。缺市值请另跑
``backfill_daily_meta``；本脚本只在已有 ``float_mv`` 的行推导换手。

用法：
    poetry run python -m scripts.data.align_daily_units --home D:\\ProgramData\\.quant --dry-run
    poetry run python -m scripts.data.align_daily_units --home D:\\ProgramData\\.quant --apply
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from common.progress_log import log_progress, log_progress_done, log_progress_error, log_progress_start
from quant.data.units import (
    align_volume_to_shares,
    fill_pre_close,
    fill_turnover_from_mv,
    last_close_by_code,
    lots_volume_mask,
    volume_amount_ratio,
)
from scripts.cli_home import add_home_argument, home_context

_SCOPE = "align_daily_units"


def _year_paths() -> list:
    from quant.data.store import daily_raw_dir

    root = daily_raw_dir()
    if not root.is_dir():
        return []
    return sorted(root.glob("year=*/part.parquet"))


def _rewrite_year(path, df: pd.DataFrame) -> None:
    from quant.data.store import _file_lock, _write_parquet_atomic

    path.parent.mkdir(parents=True, exist_ok=True)
    g = df.sort_values(["code", "date"]).reset_index(drop=True)
    with _file_lock(path):
        _write_parquet_atomic(path, g)


def _align_year(df: pd.DataFrame, *, seed: dict[str, float]) -> tuple[pd.DataFrame, dict[str, int]]:
    n = len(df)
    lots = lots_volume_mask(df) if n else pd.Series(dtype=bool)
    n_lots = int(lots.sum()) if n else 0
    out = align_volume_to_shares(df)
    before_pc = pd.Series(False, index=out.index)
    if "pre_close" in out.columns:
        before_pc = out["pre_close"].isna() | (out["pre_close"].astype(str) == "")
    elif n:
        before_pc = pd.Series(True, index=out.index)
    out = fill_pre_close(out, seed=seed)
    n_pc = 0
    if n and "pre_close" in out.columns:
        after_pc = out["pre_close"].isna() | (out["pre_close"].astype(str) == "")
        n_pc = int((before_pc & ~after_pc).sum())
    before_tr = pd.Series(False, index=out.index)
    if "turnover_rate" in out.columns:
        before_tr = out["turnover_rate"].isna() | (out["turnover_rate"].astype(str) == "")
    elif n:
        before_tr = pd.Series(True, index=out.index)
    out = fill_turnover_from_mv(out)
    n_tr = 0
    if n and "turnover_rate" in out.columns:
        after_tr = out["turnover_rate"].isna() | (out["turnover_rate"].astype(str) == "")
        n_tr = int((before_tr & ~after_tr).sum())
    ratio = volume_amount_ratio(out)
    med = float(ratio.median()) if ratio.notna().any() else float("nan")
    return out, {
        "rows": n,
        "lots": n_lots,
        "pre_close": n_pc,
        "turnover": n_tr,
        "median_ratio": med,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="对齐 daily_raw 成交量为股，并补 pre_close/换手")
    add_home_argument(ap)
    ap.add_argument("--dry-run", action="store_true", help="只统计，不写盘")
    ap.add_argument("--apply", action="store_true", help="按年分区重写 daily_raw")
    args = ap.parse_args()
    if args.apply and args.dry_run:
        print("不能同时 --apply 与 --dry-run", file=sys.stderr)
        sys.exit(2)
    apply = bool(args.apply)
    if not apply:
        args.dry_run = True

    log_progress_start(_SCOPE, "开始", detail=f"home={args.home or '当前'} mode={'APPLY' if apply else 'DRY-RUN'}")
    try:
        from quant.data.store import _read_parquet_safe

        with home_context(args.home):
            paths = _year_paths()
            if not paths:
                print("[align] daily_raw 分区不存在", file=sys.stderr)
                log_progress_error(_SCOPE, "失败", detail="无分区")
                sys.exit(1)
            print(
                f"[align] home={args.home or '当前'} mode={'APPLY' if apply else 'DRY-RUN'} "
                f"years={len(paths)}",
                flush=True,
            )
            seed: dict[str, float] = {}
            tot_lots = tot_pc = tot_tr = tot_rows = 0
            for i, path in enumerate(paths, 1):
                year = path.parent.name.split("=", 1)[-1]
                df = _read_parquet_safe(path, empty_columns=["code", "date"])
                aligned, stats = _align_year(df, seed=seed)
                tot_rows += stats["rows"]
                tot_lots += stats["lots"]
                tot_pc += stats["pre_close"]
                tot_tr += stats["turnover"]
                med = stats["median_ratio"]
                med_s = f"{med:.3f}" if med == med else "nan"
                line = (
                    f"  {year}  rows={stats['rows']:,}  lots→股={stats['lots']:,}  "
                    f"pre_close+={stats['pre_close']:,}  turnover+={stats['turnover']:,}  "
                    f"median amount/(vol×close)={med_s}"
                )
                print(line, flush=True)
                log_progress(_SCOPE, f"{year} {i}/{len(paths)}", detail=line.strip())
                if apply and not df.empty:
                    _rewrite_year(path, aligned)
                if not aligned.empty:
                    seed = last_close_by_code(aligned)
            print(
                f"[align] 合计 rows={tot_rows:,}  lots→股={tot_lots:,}  "
                f"pre_close+={tot_pc:,}  turnover+={tot_tr:,}",
                flush=True,
            )
            if apply:
                print("[align] 已按年重写 daily_raw；可再跑 validate_library / backfill_daily_meta")
            else:
                print("[align] DRY-RUN 未写盘；确认后加 --apply")
            log_progress_done(
                _SCOPE,
                "成功",
                detail=f"lots={tot_lots} pre_close={tot_pc} turnover={tot_tr} apply={apply}",
            )
    except SystemExit:
        raise
    except Exception as e:
        log_progress_error(_SCOPE, "失败", detail=f"{type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    main()
