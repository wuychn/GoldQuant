"""量化流水线进度日志：带时间戳输出到 stdout，便于 CLI / API / 定时任务查看进度。"""

from __future__ import annotations

import logging
import sys
from datetime import datetime

_CONFIGURED = False
_logger = logging.getLogger("quant.progress")


class _FlushingStreamHandler(logging.StreamHandler):
    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


def configure_progress_logging() -> None:
    """配置 progress logger（幂等）。"""
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = _FlushingStreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(message)s"),
    )
    _logger.setLevel(logging.INFO)
    _logger.handlers.clear()
    _logger.addHandler(handler)
    _logger.propagate = False
    _CONFIGURED = True


def log_progress(scope: str, message: str, *, detail: str = "") -> None:
    """输出一行进度：``[HH:MM:SS] [scope] message — detail``。"""
    configure_progress_logging()
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] [{scope}] {message}"
    if detail:
        line = f"{line} — {detail}"
    _logger.info(line)


def log_progress_count(scope: str, message: str, current: int, total: int, *, detail: str = "") -> None:
    """带 ``current/total`` 的进度行。"""
    suffix = f" ({current}/{total})"
    extra = f"{detail}{suffix}" if detail else suffix.lstrip()
    log_progress(scope, message, detail=extra)


def log_progress_done(scope: str, message: str = "完成", *, detail: str = "") -> None:
    log_progress(scope, message, detail=detail)
