"""Per-source sync/async rate limiters (lazy init, config-driven)."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")

_limiters: dict[tuple[str, str], threading.Semaphore | asyncio.Semaphore] = {}
_limiter_lock = threading.Lock()


def _max_concurrent(source: str) -> int:
    from quant.config import load_quant_config

    data = load_quant_config().get("data") or {}
    key = f"{source}_max_concurrent"
    if key in data:
        return max(1, int(data[key]))
    default = data.get("default_max_concurrent", 1)
    return max(1, int(default))


def _get_limiter(source: str, *, async_mode: bool) -> threading.Semaphore | asyncio.Semaphore:
    key = (source, "async" if async_mode else "sync")
    with _limiter_lock:
        lim = _limiters.get(key)
        if lim is None:
            n = _max_concurrent(source)
            lim = asyncio.Semaphore(n) if async_mode else threading.Semaphore(n)
            _limiters[key] = lim
        return lim


def reset_limiters() -> None:
    """Clear cached limiters (tests / override_quant_home)."""
    with _limiter_lock:
        _limiters.clear()


def get_sync_limiter(source: str) -> threading.Semaphore:
    lim = _get_limiter(source, async_mode=False)
    assert isinstance(lim, threading.Semaphore)
    return lim


def get_async_limiter(source: str) -> asyncio.Semaphore:
    lim = _get_limiter(source, async_mode=True)
    assert isinstance(lim, asyncio.Semaphore)
    return lim


def with_limit(source: str, fn: Callable[[], T]) -> T:
    sem = get_sync_limiter(source)
    with sem:
        return fn()


async def alimit(source: str, coro_factory: Callable[[], Awaitable[T]]) -> T:
    sem = get_async_limiter(source)
    async with sem:
        return await coro_factory()
