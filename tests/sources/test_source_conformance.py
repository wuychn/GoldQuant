"""Source conformance tests：四类数据源协议（Daily/Market/Enrich/Info）。"""

from __future__ import annotations

from quant.data.sources import (
    get_daily_source,
    get_enrich_source,
    get_info_source,
    get_market_source,
)
from quant.data.sources.protocols import (
    DailySource,
    EnrichSource,
    InfoSource,
    MarketSource,
)


def test_four_source_protocols_conformance():
    """四类 default 实现均符合各自协议（runtime_checkable 结构校验）。"""
    assert isinstance(get_daily_source(), DailySource)
    assert isinstance(get_market_source(), MarketSource)
    assert isinstance(get_enrich_source(), EnrichSource)
    assert isinstance(get_info_source(), InfoSource)


def test_default_source_names():
    assert get_daily_source().name == "default"
    assert get_market_source().name == "default"
    assert get_enrich_source().name == "default"
    assert get_info_source().name == "default"


def test_rate_limit_lazy_init():
    from quant.data.sources.rate_limit import get_sync_limiter, reset_limiters

    reset_limiters()
    a = get_sync_limiter("akshare")
    b = get_sync_limiter("akshare")
    assert a is b
    reset_limiters()
