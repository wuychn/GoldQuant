"""日数据维护：无库→建库；有库→查漏补漏；最后跑当日增量。

由调度器 16:00（收盘后）触发，目标是离线库自愈，平时无需手动 build/update。
``build_daily`` 长跑用子进程隔离，失败不阻断后续步骤（补漏失败仍尝试当日增量）。

逻辑：
    1. read_daily_raw(end=as_of) 为空 → build_daily 全量（断点续传）
    2. 非空 → scan_missing_dates 比对交易日历找缺口 → 有则 build_daily --ignore-existing 回补
    3. 无论建/补，最后跑 update_daily 当日增量（spot_em + 指数/行业/universe）

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

from quant.data.calendar import is_trading_day
from quant.data.store import read_calendar, read_daily_raw
from quant.timeutil import cn_now

DEFAULT_START = "2021-01-01"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
    """子进程跑 ``python -m <module> <extra>``，返回退出码；失败不抛。"""
    cmd = [sys.executable, "-m", module, *extra]
    print(f"[maintain] 执行: {' '.join(cmd)}")
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(_PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as e:  # noqa: BLE001
        print(f"[maintain][FATAL] 子进程启动失败 {module}: {e}", file=sys.stderr)
        return -1
    if proc.stdout:
        print(proc.stdout)
    if proc.returncode != 0:
        tail = " | ".join(
            line.strip() for line in (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
            if line.strip()
        ) or "(无输出)"
        print(f"[maintain][WARN] {module} 退出码={proc.returncode} — {tail}", file=sys.stderr)
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
            print("找不到交易日，退出")
            sys.exit(1)
        today = prev
    as_of = today.isoformat()
    print(f"[maintain] 维护日 as_of={as_of}")

    daily = read_daily_raw(end=as_of)
    if daily.empty:
        print(f"[maintain] 离线库为空 → 全量建库 build_daily --start {args.start} --end {as_of}")
        _invoke_module("scripts.data.build_daily", ["--start", args.start, "--end", as_of])
    else:
        missing = scan_missing_dates(as_of)
        if missing:
            print(
                f"[maintain] 检测到 {len(missing)} 个缺口交易日: "
                f"{missing[0]} ~ {missing[-1]} → 回补（--ignore-existing）"
            )
            _invoke_module(
                "scripts.data.build_daily",
                ["--start", missing[0], "--end", missing[-1], "--ignore-existing"],
            )
        else:
            print("[maintain] 历史数据完整，无缺口")

    print(f"[maintain] 当日增量 update_daily --date {as_of}")
    _invoke_module("scripts.data.update_daily", ["--date", as_of])
    print("[maintain] 完成")


if __name__ == "__main__":
    main()
