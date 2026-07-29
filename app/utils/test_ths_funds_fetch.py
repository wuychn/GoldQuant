"""同花顺个股资金流拉取与 enrich 分批。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services.stock_enrich import _resolve_enrich_runtime
from app.utils.error_log import format_error_detail, http_status_from_exception
from quant.data.sources.ths.funds import (
    ThsFundsFetchError,
    _retry_delay_sec,
    fetch_stock_funds_cached,
    reset_ths_funds_cache_for_tests,
)


class _SettingsStub:
    QUANT_THS_FUNDS_CACHE_TTL_SEC = 120
    QUANT_THS_FUNDS_RETRY_MAX = 3
    QUANT_THS_FUNDS_RETRY_BASE_SEC = 1.0
    QUANT_THS_FUNDS_FAILURE_BACKOFF_SEC = 5.0
    QUANT_ENRICH_CONCURRENCY = 6
    QUANT_ENRICH_EVENING_CONCURRENCY = 6
    QUANT_ENRICH_EVENING_BATCH_SIZE = 20
    QUANT_ENRICH_EVENING_BATCH_PAUSE_SEC = 600


@pytest.fixture(autouse=True)
def _clear_funds_cache() -> None:
    reset_ths_funds_cache_for_tests()


def test_retry_delay_grows_with_failures() -> None:
    d0 = _retry_delay_sec(0, base_sec=1.0, recent_failures=0, failure_backoff_sec=5.0)
    d1 = _retry_delay_sec(1, base_sec=1.0, recent_failures=0, failure_backoff_sec=5.0)
    d_fail = _retry_delay_sec(0, base_sec=1.0, recent_failures=3, failure_backoff_sec=5.0)
    assert d0 == 1.0
    assert d1 == 2.0
    assert d_fail == 16.0


def test_evening_enrich_runtime_defaults() -> None:
    conc, batch, pause = _resolve_enrich_runtime(_SettingsStub(), "post_market_evening", None)
    assert conc == 6
    assert batch == 20
    assert pause == 600


def test_intraday_enrich_no_batch() -> None:
    conc, batch, pause = _resolve_enrich_runtime(_SettingsStub(), "during_market", None)
    assert conc == 6
    assert batch is None
    assert pause == 0


def test_http_status_from_httpx() -> None:
    req = httpx.Request("GET", "https://example.com")
    resp = httpx.Response(429, request=req, text="rate limit")
    exc = httpx.HTTPStatusError("429", request=req, response=resp)
    assert http_status_from_exception(exc) == 429
    detail = format_error_detail("test", exc)
    assert "HTTP 429" in detail


def test_fetch_uses_cache_within_ttl() -> None:
    async def _run() -> None:
        payload = {"flash": [], "title": {"zlr": 1, "zlc": 2, "je": 3}}
        with patch("quant.data.sources.ths.funds._fetch_once", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = payload
            with patch("app.core.config.get_settings", return_value=_SettingsStub()):
                a = await fetch_stock_funds_cached("600519", ttl_sec=60)
                b = await fetch_stock_funds_cached("600519", ttl_sec=60)
        assert a == b == payload
        assert mock_fetch.await_count == 1

    asyncio.run(_run())


def test_fetch_retries_then_raises_with_status() -> None:
    async def _run() -> None:
        req = httpx.Request("GET", "https://stockpage.10jqka.com.cn/x")
        resp = httpx.Response(503, request=req, text="busy")
        err = httpx.HTTPStatusError("503", request=req, response=resp)

        with patch("quant.data.sources.ths.funds._fetch_once", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = err
            with patch("app.core.config.get_settings", return_value=_SettingsStub()):
                with patch("quant.data.sources.ths.funds.asyncio.sleep", new_callable=AsyncMock):
                    with pytest.raises(ThsFundsFetchError) as ei:
                        await fetch_stock_funds_cached("600192", ttl_sec=0, max_retries=2)
        assert ei.value.http_status == 503
        assert mock_fetch.await_count == 2

    asyncio.run(_run())
