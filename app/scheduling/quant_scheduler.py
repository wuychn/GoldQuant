"""进程内不再挂量化任务。复盘 20:10、盘中 09:30 由 Windows 计划任务执行。"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)


def build_quant_scheduler() -> BackgroundScheduler | None:
    logger.info("[quant-scheduler] 复盘与盘中由 Windows 任务执行，进程内不注册")
    return None


def shutdown_quant_scheduler(sched: BackgroundScheduler | None) -> None:
    if sched is not None and sched.running:
        sched.shutdown(wait=False)
