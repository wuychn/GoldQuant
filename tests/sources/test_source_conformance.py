"""Source conformance tests (Phase 1a / #12)."""

from __future__ import annotations

import pytest

from quant.data.sources.akshare.index import AkshareIndexSource
from quant.data.sources.akshare.spot import AkshareSpotSource
from quant.data.sources.fixture.spot import FixtureSpotSource
from quant.data.sources.protocols import IndexSource, SpotSource


def test_fixture_spot_conformance():
    src = FixtureSpotSource()
    assert isinstance(src, SpotSource)
    rows = src.fetch_all()
    assert isinstance(rows, list)


def test_akshare_spot_protocol():
    src = AkshareSpotSource()
    assert isinstance(src, SpotSource)


def test_akshare_index_protocol():
    src = AkshareIndexSource()
    assert isinstance(src, IndexSource)


def test_rate_limit_lazy_init():
    from quant.data.sources.rate_limit import get_sync_limiter, reset_limiters

    reset_limiters()
    a = get_sync_limiter("akshare")
    b = get_sync_limiter("akshare")
    assert a is b
    reset_limiters()
