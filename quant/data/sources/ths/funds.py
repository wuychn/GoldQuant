"""同花顺个股实时资金流：TTL 缓存、退避重试、累计失败加长等待。"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from quant.data.sources.ths.hexin import (
    CHROME_USER_AGENT,
    HTTP_TIMEOUT,
    STOCK_FUNDS_URL,
    browser_common_headers,
    get_v,
)

logger = logging.getLogger(__name__)

_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_cache_lock = asyncio.Lock()
_recent_failures = 0
_failure_lock = asyncio.Lock()


class ThsFundsFetchError(Exception):
    """同花顺个股资金流拉取失败。"""

    def __init__(
        self,
        message: str,
        *,
        symbol: str = "",
        http_status: int | None = None,
        attempt: int = 0,
        retries: int = 0,
    ) -> None:
        super().__init__(message)
        self.symbol = symbol
        self.http_status = http_status
        self.attempt = attempt
        self.retries = retries


def _funds_headers() -> dict[str, str]:
    return {
        "User-Agent": CHROME_USER_AGENT,
        **browser_common_headers(),
        "Cookie": "keep-alive",
        "Hexin-V": get_v(),
        "Host": "stockpage.10jqka.com.cn",
        "Referer": "https://stockpage.10jqka.com.cn/002580/funds/",
    }


def _http_status_from_exc(exc: BaseException) -> int | None:
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        return int(exc.response.status_code)
    cause = exc.__cause__
    if cause is not None and cause is not exc:
        return _http_status_from_exc(cause)
    return None


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
    logger.debug("同花顺资金流累计失败 %d 次，请求前等待 %.1fs", n, wait)
    await asyncio.sleep(wait)


def _retry_delay_sec(
    attempt: int,
    *,
    base_sec: float,
    recent_failures: int,
    failure_backoff_sec: float,
) -> float:
    return base_sec * (2**attempt) + recent_failures * failure_backoff_sec


async def _fetch_once(symbol: str) -> dict[str, Any]:
    url = STOCK_FUNDS_URL.format(symbol=symbol)
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.get(url, headers=_funds_headers())
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, dict):
        raise ThsFundsFetchError(
            f"响应非 JSON 对象: {type(data).__name__}",
            symbol=symbol,
        )
    return data


async def fetch_stock_funds_cached(
    symbol: str,
    *,
    ttl_sec: float | None = None,
    max_retries: int | None = None,
    retry_base_sec: float | None = None,
    failure_backoff_sec: float | None = None,
    max_failure_penalty_sec: float = 300.0,
) -> dict[str, Any]:
    """拉取同花顺个股实时资金流（带缓存与退避重试）。"""
    from app.core.config import get_settings

    settings = get_settings()
    sym = str(symbol).strip()
    if not sym:
        raise ThsFundsFetchError("symbol 为空")

    ttl = float(ttl_sec if ttl_sec is not None else settings.QUANT_THS_FUNDS_CACHE_TTL_SEC)
    retries = int(max_retries if max_retries is not None else settings.QUANT_THS_FUNDS_RETRY_MAX)
    base = float(retry_base_sec if retry_base_sec is not None else settings.QUANT_THS_FUNDS_RETRY_BASE_SEC)
    fail_bo = float(
        failure_backoff_sec
        if failure_backoff_sec is not None
        else settings.QUANT_THS_FUNDS_FAILURE_BACKOFF_SEC
    )

    now = time.monotonic()
    if ttl > 0:
        async with _cache_lock:
            hit = _cache.get(sym)
            if hit and hit[0] > now:
                return hit[1]

    await _failure_penalty_sleep(failure_backoff_sec=fail_bo, max_penalty_sec=max_failure_penalty_sec)

    last_exc: BaseException | None = None
    last_status: int | None = None
    for attempt in range(max(1, retries)):
        if attempt > 0:
            async with _failure_lock:
                n = _recent_failures
            delay = _retry_delay_sec(attempt - 1, base_sec=base, recent_failures=n, failure_backoff_sec=fail_bo)
            logger.debug(
                "同花顺资金流重试 symbol=%s attempt=%d/%d 等待 %.1fs",
                sym,
                attempt + 1,
                retries,
                delay,
            )
            await asyncio.sleep(delay)

        try:
            data = await _fetch_once(sym)
        except Exception as exc:
            last_exc = exc
            last_status = _http_status_from_exc(exc)
            await _record_failure()
            continue

        await _record_success()
        if ttl > 0:
            expires = time.monotonic() + ttl
            async with _cache_lock:
                _cache[sym] = (expires, data)
        return data

    status_part = f" HTTP {last_status}" if last_status is not None else ""
    msg = f"同花顺个股资金流失败 symbol={sym}{status_part} 已重试 {retries} 次"
    if last_exc is not None:
        detail = str(last_exc).strip() or repr(last_exc)
        msg = f"{msg}: {detail}"
    raise ThsFundsFetchError(
        msg,
        symbol=sym,
        http_status=last_status,
        attempt=retries,
        retries=retries,
    ) from last_exc


def reset_ths_funds_cache_for_tests() -> None:
    """测试用：清空缓存与失败计数。"""
    global _recent_failures
    _cache.clear()
    _recent_failures = 0
