"""数据源注册表：按 ``quant.yml`` 的 ``data.sources.{daily,market,enrich,info}`` 选实现。

旧 spot/index/news 三类已并入 Daily/Market/Info（语义：fetch_spot/fetch_index_spot/
fetch_news），故不再单列协议/注册表。
"""

from __future__ import annotations

from typing import Any

from quant.data.sources.daily.akshare import AkshareDailySource
from quant.data.sources.daily.default import DefaultDailySource
from quant.data.sources.enrich.default import DefaultEnrichSource
from quant.data.sources.info.default import DefaultInfoSource
from quant.data.sources.market.default import DefaultMarketSource

DAILY_REGISTRY: dict[str, type] = {
    "default": DefaultDailySource,
    "akshare": AkshareDailySource,
}

MARKET_REGISTRY: dict[str, type] = {
    "default": DefaultMarketSource,
}

ENRICH_REGISTRY: dict[str, type] = {
    "default": DefaultEnrichSource,
}

INFO_REGISTRY: dict[str, type] = {
    "default": DefaultInfoSource,
}


def get_daily_source_from_registry(name: str) -> Any:
    cls = DAILY_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"未知 daily source: {name}")
    return cls()


def get_market_source_from_registry(name: str) -> Any:
    cls = MARKET_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"未知 market source: {name}")
    return cls()


def get_enrich_source_from_registry(name: str) -> Any:
    cls = ENRICH_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"未知 enrich source: {name}")
    return cls()


def get_info_source_from_registry(name: str) -> Any:
    cls = INFO_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"未知 info source: {name}")
    return cls()
