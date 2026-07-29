"""Second live source registry (Phase 5): switch via quant.yml data.sources."""

from __future__ import annotations

from typing import Any

from quant.data.sources.akshare.index import AkshareIndexSource
from quant.data.sources.akshare.news import AkshareNewsSource
from quant.data.sources.fixture.index import FixtureIndexSource
from quant.data.sources.fixture.news import FixtureNewsSource
from quant.data.sources.fixture.spot import FixtureSpotSource
from quant.data.sources.akshare.spot import AkshareSpotSource

SPOT_REGISTRY: dict[str, type] = {
    "akshare": AkshareSpotSource,
    "fixture": FixtureSpotSource,
}

INDEX_REGISTRY: dict[str, type] = {
    "akshare": AkshareIndexSource,
    "fixture": FixtureIndexSource,
}

NEWS_REGISTRY: dict[str, type] = {
    "akshare": AkshareNewsSource,
    "fixture": FixtureNewsSource,
}


def get_spot_source_from_registry(name: str) -> Any:
    cls = SPOT_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"未知 spot source: {name}")
    return cls()


def get_index_source_from_registry(name: str) -> Any:
    cls = INDEX_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"未知 index source: {name}")
    return cls()


def get_news_source_from_registry(name: str) -> Any:
    cls = NEWS_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"未知 news source: {name}")
    return cls()
