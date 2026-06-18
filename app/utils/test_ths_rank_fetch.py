"""同花顺形态榜 / 初筛拉取退避重试。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.utils.ths_rank_fetch import (
    _retry_delay_sec,
    fetch_with_retry,
    reset_ths_rank_fetch_state_for_tests,
)


class _SettingsStub:
    QUANT_THS_FUNDS_RETRY_MAX = 3
    QUANT_THS_FUNDS_RETRY_BASE_SEC = 0.01
    QUANT_THS_FUNDS_FAILURE_BACKOFF_SEC = 0.01


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    reset_ths_rank_fetch_state_for_tests()


def test_retry_delay_grows_with_failures() -> None:
    assert _retry_delay_sec(0, base_sec=1.0, recent_failures=0, failure_backoff_sec=5.0) == 1.0
    assert _retry_delay_sec(1, base_sec=1.0, recent_failures=0, failure_backoff_sec=5.0) == 2.0
    assert _retry_delay_sec(0, base_sec=1.0, recent_failures=3, failure_backoff_sec=5.0) == 16.0


def test_fetch_returns_default_after_retries_exhausted() -> None:
    async def _run() -> None:
        fn = AsyncMock(side_effect=ConnectionError("connection aborted"))
        with patch("app.core.config.get_settings", return_value=_SettingsStub()):
            result = await fetch_with_retry("量价齐升", fn, default=[], progress_scope="test")
        assert result == []
        assert fn.await_count == 3

    asyncio.run(_run())


def test_fetch_succeeds_on_second_attempt() -> None:
    async def _run() -> None:
        fn = AsyncMock(side_effect=[ConnectionError("fail"), [{"code": "600519"}]])
        with patch("app.core.config.get_settings", return_value=_SettingsStub()):
            result = await fetch_with_retry("持续上涨", fn, default=[], progress_scope="test")
        assert result == [{"code": "600519"}]
        assert fn.await_count == 2

    asyncio.run(_run())
