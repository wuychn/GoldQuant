"""同花顺 HTTP 拉取：退避重试；耗尽后返回默认值，不向上抛异常。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from common.progress_log import log_progress_error

logger = logging.getLogger(__name__)

T = TypeVar("T")

_recent_failures = 0
_failure_lock = asyncio.Lock()


async def _record_failure() -> None:
    global _recent_failures
    async with _failure_lock:
        _recent_failures += 1


async def _record_success() -> None:
    global _recent_failures
    async with _failure_lock:
        if _recent_failures > 0:
            _recent_failures -= 1


async def _failure_penalty_sleep(*, failure_backoff_sec: float, max_penalty_sec: float) -> None:
    async with _failure_lock:
        n = _recent_failures
    if n <= 0:
        return
    wait = min(max_penalty_sec, n * failure_backoff_sec)
    logger.debug("同花顺拉取累计失败 %d 次，请求前等待 %.1fs", n, wait)
    await asyncio.sleep(wait)


def _retry_delay_sec(
    attempt: int,
    *,
    base_sec: float,
    recent_failures: int,
    failure_backoff_sec: float,
) -> float:
    return base_sec * (2**attempt) + recent_failures * failure_backoff_sec


async def fetch_with_retry(
    label: str,
    fetch_fn: Callable[[], Awaitable[T]],
    *,
    default: T,
    progress_scope: str = "ths_fetch",
    max_retries: int | None = None,
    retry_base_sec: float | None = None,
    failure_backoff_sec: float | None = None,
    max_failure_penalty_sec: float = 300.0,
) -> T:
    """执行 ``fetch_fn``，失败则指数退避重试；全部失败后记录错误并返回 ``default``。"""
    from common.config import get_settings

    settings = get_settings()
    retries = int(max_retries if max_retries is not None else settings.QUANT_THS_FUNDS_RETRY_MAX)
    base = float(retry_base_sec if retry_base_sec is not None else settings.QUANT_THS_FUNDS_RETRY_BASE_SEC)
    fail_bo = float(
        failure_backoff_sec
        if failure_backoff_sec is not None
        else settings.QUANT_THS_FUNDS_FAILURE_BACKOFF_SEC
    )

    await _failure_penalty_sleep(failure_backoff_sec=fail_bo, max_penalty_sec=max_failure_penalty_sec)

    last_exc: BaseException | None = None
    for attempt in range(max(1, retries)):
        if attempt > 0:
            async with _failure_lock:
                n = _recent_failures
            delay = _retry_delay_sec(attempt - 1, base_sec=base, recent_failures=n, failure_backoff_sec=fail_bo)
            logger.warning(
                "同花顺拉取重试 label=%s attempt=%d/%d 等待 %.1fs",
                label,
                attempt + 1,
                retries,
                delay,
            )
            await asyncio.sleep(delay)

        try:
            result = await fetch_fn()
        except Exception as exc:
            last_exc = exc
            await _record_failure()
            continue

        await _record_success()
        return result

    detail = str(last_exc).strip() if last_exc is not None else "未知错误"
    if len(detail) > 200:
        detail = detail[:197] + "..."
    log_progress_error(progress_scope, f"拉取失败：{label}", detail=f"已重试 {retries} 次 — {detail}")
    return default


def reset_ths_rank_fetch_state_for_tests() -> None:
    """测试用：清零累计失败计数。"""
    global _recent_failures
    _recent_failures = 0
