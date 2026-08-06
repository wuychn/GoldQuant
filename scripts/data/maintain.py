"""每周离线库自愈：无库→建库；有库→查漏补漏；最后跑当日增量。

由调度器每周五 22:00（``maintain_weekly_time``）触发，目标是离线库自愈。
日常的当日增量由 18:00 ``update_daily`` 单独跑（分钟级），避免 ``build_daily`` 长跑阻塞。
``build_daily`` 长跑用子进程隔离，失败不阻断后续步骤（补漏失败仍尝试当日增量）。

逻辑：
    1. read_daily_raw(end=as_of) 为空 → build_daily 全量（断点续传）
    2. 非空 → scan_missing_dates 比对交易日历找缺口 → 有则 build_daily --ignore-existing 回补
    3. 无论建/补，最后跑 update_daily 当日增量（spot_em + 指数/行业/universe）
    4. build_daily --retry-failed 重试失败清单

用法：
    python -m scripts.data.maintain
    python -m scripts.data.maintain --date 2026-07-25
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from common.progress_log import (
    log_progress,
    log_progress_done,
    log_progress_error,
    log_progress_start,
)
from common.timeutil import cn_now
from quant.data.calendar import is_trading_day
from quant.data.store import read_calendar, read_daily_raw

DEFAULT_START = "2021-01-01"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SCOPE = "maintain"


def _prev_trading_day(d: date) -> date | None:
    cur = d - timedelta(days=1)
    for _ in range(15):
        if is_trading_day(cur):
            return cur
        cur -= timedelta(days=1)
    return None


def scan_missing_dates(as_of: str) -> list[str]:
    """对比交易日历 vs daily_raw 已有日期，返回缺失的交易日（升序，截至 as_of）。"""
    daily = read_daily_raw(end=as_of)
    have = set(daily["date"].astype(str).unique()) if not daily.empty else set()
    cal = read_calendar()
    return [d for d in cal if d <= as_of and d not in have]


def _invoke_module(module: str, extra: list[str]) -> int:
    """子进程跑 ``python -m <module> <extra>``，实时透传输出；返回退出码。"""
    cmd = [sys.executable, "-m", module, *extra]
    log_progress(_SCOPE, "执行子任务", detail=" ".join(cmd))
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        # 不 capture：子进程进度实时可见（build_daily 长跑尤其需要）
        proc = subprocess.run(cmd, cwd=str(_PROJECT_ROOT), env=env)
    except Exception as e:  # noqa: BLE001
        log_progress_error(_SCOPE, "子进程启动失败", detail=f"{module}: {e}")
        return -1
    if proc.returncode != 0:
        log_progress_error(
            _SCOPE,
            "子任务失败",
            detail=f"{module} 退出码={proc.returncode}",
        )
    else:
        log_progress(_SCOPE, "子任务成功", detail=module)
    return proc.returncode


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="维护截至日 YYYY-MM-DD，默认今天（非交易日回退最近交易日）")
    ap.add_argument("--start", default=DEFAULT_START, help=f"无库时全量起始日，默认 {DEFAULT_START}")
    args = ap.parse_args()

    today = date.fromisoformat(args.date) if args.date else cn_now().date()
    if not is_trading_day(today):
        prev = _prev_trading_day(today)
        if prev is None:
            log_progress_error(_SCOPE, "找不到交易日，退出")
            sys.exit(1)
        today = prev
    as_of = today.isoformat()
    log_progress_start(_SCOPE, "开始", detail=f"as_of={as_of} start={args.start}")

    fails: list[str] = []
    daily = read_daily_raw(end=as_of)
    if daily.empty:
        log_progress(_SCOPE, "离线库为空 → 全量建库", detail=f"build_daily --start {args.start} --end {as_of}")
        rc = _invoke_module("scripts.data.build_daily", ["--start", args.start, "--end", as_of])
        if rc != 0:
            fails.append("build_daily(全量)")
    else:
        log_progress(_SCOPE, "扫描交易日缺口 …")
        missing = scan_missing_dates(as_of)
        if missing:
            log_progress(
                _SCOPE,
                "检测到缺口，回补",
                detail=f"{len(missing)} 日 {missing[0]}~{missing[-1]}",
            )
            rc = _invoke_module(
                "scripts.data.build_daily",
                ["--start", missing[0], "--end", missing[-1], "--ignore-existing"],
            )
            if rc != 0:
                fails.append("build_daily(补漏)")
        else:
            log_progress(_SCOPE, "历史数据完整，无缺口")

    log_progress(_SCOPE, "当日增量", detail=f"update_daily --date {as_of}")
    rc = _invoke_module("scripts.data.update_daily", ["--date", as_of])
    if rc != 0:
        fails.append("update_daily")

    log_progress(_SCOPE, "重试失败清单", detail="build_daily --retry-failed")
    rc = _invoke_module("scripts.data.build_daily", ["--retry-failed"])
    if rc != 0:
        fails.append("build_daily(retry-failed)")

    if fails:
        log_progress_error(_SCOPE, "完成但有失败步骤", detail=", ".join(fails))
        sys.exit(1)
    log_progress_done(_SCOPE, "成功", detail=f"as_of={as_of}")


if __name__ == "__main__":
    main()
