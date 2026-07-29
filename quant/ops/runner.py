"""r3 运维入口：直调 service → 生成正文 → 推送。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from quant.jobs.market_jobs import run_market_job
from common.progress_log import log_progress, log_progress_done, log_progress_error

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def run_mode(mode: str, timestamp: str = "", *, push: bool = True) -> str:
    log_progress(mode, f"开始 job {mode}")
    try:
        msg = run_market_job(mode, push=push)
    except Exception as e:
        log_progress_error(mode, "job 失败", detail=str(e))
        raise
    print(msg)
    return msg


def run_daily_decision(*, push: bool = True, dry_run: bool = False) -> str:
    """子进程跑日决策（默认纸面成交），再读取最新报告推送。"""
    cmd = [sys.executable, "-m", "scripts.decision.daily"]
    if dry_run:
        cmd.append("--dry-run")
    if not push:
        cmd.append("--no-push")
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    log_progress("daily_decision", "执行日决策纸面撮合")
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
            "日决策失败",
            detail=(proc.stderr or proc.stdout or "")[-800:],
        )
        raise SystemExit(proc.returncode)
    log_progress_done("daily_decision", "日决策完成")
    return proc.stdout or ""
