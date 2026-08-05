"""数据源注册表：按 ``quant.yml`` 的 ``data.sources.{daily,market,enrich,info}`` 选实现。

旧 spot/index/news 三类已并入 Daily/Market/Info（语义：fetch_spot/fetch_index_spot/
fetch_news），故不再单列协议/注册表。
"""

from __future__ import annotations

from typing import Any

from quant.data.sources.daily.akshare import AkshareDailySource
from quant.data.sources.daily.default import DefaultDailySource
from quant.data.sources.daily.sina import SinaDailySource
from quant.data.sources.daily.tencent import TencentDailySource
from quant.data.sources.enrich.akshare import AkshareEnrichSource
from quant.data.sources.enrich.default import DefaultEnrichSource
from quant.data.sources.enrich.eastmoney import EastmoneyEnrichSource
from quant.data.sources.enrich.ths import ThsEnrichSource
from quant.data.sources.enrich.wencai import WencaiEnrichSource
from quant.data.sources.info.akshare import AkshareInfoSource
from quant.data.sources.info.default import DefaultInfoSource
from quant.data.sources.info.eastmoney import EastmoneyInfoSource
from quant.data.sources.market.akshare import AkshareMarketSource
from quant.data.sources.market.default import DefaultMarketSource
from quant.data.sources.market.eastmoney import EastmoneyMarketSource
from quant.data.sources.market.sina import SinaMarketSource
from quant.data.sources.market.ths import ThsMarketSource

DAILY_REGISTRY: dict[str, type] = {
    "default": DefaultDailySource,
    "akshare": AkshareDailySource,
    "sina": SinaDailySource,
    "tencent": TencentDailySource,
}

MARKET_REGISTRY: dict[str, type] = {
    "default": DefaultMarketSource,
    "akshare": AkshareMarketSource,
    "eastmoney": EastmoneyMarketSource,
    "ths": ThsMarketSource,
    "sina": SinaMarketSource,
}

ENRICH_REGISTRY: dict[str, type] = {
    "default": DefaultEnrichSource,
    "eastmoney": EastmoneyEnrichSource,
    "ths": ThsEnrichSource,
    "wencai": WencaiEnrichSource,
    "akshare": AkshareEnrichSource,
}

INFO_REGISTRY: dict[str, type] = {
    "default": DefaultInfoSource,
    "akshare": AkshareInfoSource,
    "eastmoney": EastmoneyInfoSource,
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
