"""r3 运维入口：拉取 → 生成正文 → 推送。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from quant.data_fetch import fetch_mode
from quant.ops.modes import (
    build_during_body,
    build_evening_body,
    build_lunch_body,
    build_news_body,
    build_pre_market_body,
)
from quant.ops.push import push_text
from quant.progress_log import log_progress, log_progress_done, log_progress_error

_LABELS = {
    "news": "新闻聚焦",
    "pre_market": "盘前准备",
    "during_market": "智能盯盘",
    "post_market_lunch": "午间复盘",
    "post_market_evening": "收盘复盘",
}

_BUILDERS = {
    "news": build_news_body,
    "pre_market": build_pre_market_body,
    "during_market": build_during_body,
    "post_market_lunch": build_lunch_body,
    "post_market_evening": build_evening_body,
}

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def run_mode(mode: str, timestamp: str = "", *, push: bool = True) -> str:
    if mode not in _BUILDERS:
        raise SystemExit(f"未知模式: {mode}；可用: {', '.join(_BUILDERS)}")
    label = _LABELS[mode]
    log_progress(mode, f"开始 {label}")
    try:
        raw = fetch_mode(mode)
    except Exception as e:
        log_progress_error(mode, "数据拉取失败", detail=str(e))
        raise
    try:
        body = _BUILDERS[mode](raw)
    except Exception as e:
        log_progress_error(mode, "正文生成失败", detail=str(e))
        raise
    msg = push_text(label, body, mode=mode, push=push)
    log_progress_done(mode, f"{label} 完成")
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
