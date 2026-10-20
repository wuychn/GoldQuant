"""5 板复盘入口：子进程跑 ``scripts.tactic.close``。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from common.progress_log import log_progress, log_progress_done, log_progress_error

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def run_daily_decision(*, push: bool = True, dry_run: bool = False) -> str:
    """盘后 5 板复盘，写出次日计划并推飞书。"""
    cmd = [sys.executable, "-m", "scripts.tactic.close"]
    if not push or dry_run:
        cmd.append("--no-push")
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    log_progress("daily_decision", "执行 5 板复盘")
    proc = subprocess.run(
        cmd,
        cwd=str(_PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.stdout:
        print(proc.stdout)
    if proc.returncode != 0:
        log_progress_error(
            "daily_decision",
            "复盘失败",
            detail=(proc.stderr or proc.stdout or "")[-800:],
        )
        raise SystemExit(proc.returncode)
    log_progress_done("daily_decision", "复盘完成")
    return proc.stdout or ""
