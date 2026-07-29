"""Source factory: registry + fixture mode."""

from __future__ import annotations

from quant.data.sources.protocols import IndexSource, NewsSource, SpotSource


def fixture_mode() -> bool:
    from common.config import get_settings

    return bool(get_settings().QUANT_USE_LOCAL_FIXTURE)


def _source_name(key: str, *, fixture_default: str = "fixture") -> str:
    if fixture_mode():
        return fixture_default
    from quant.config import load_quant_config

    return (load_quant_config().get("data") or {}).get("sources", {}).get(key, "akshare")


def get_spot_source() -> SpotSource:
    from quant.data.sources.registry import get_spot_source_from_registry

    return get_spot_source_from_registry(_source_name("spot"))


def get_index_source() -> IndexSource:
    from quant.data.sources.registry import get_index_source_from_registry

    return get_index_source_from_registry(_source_name("index", fixture_default="fixture"))


def get_news_source() -> NewsSource:
    from quant.data.sources.registry import get_news_source_from_registry

    return get_news_source_from_registry(_source_name("news", fixture_default="fixture"))
