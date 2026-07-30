"""Source factory: 按 ``quant.yml`` 的 ``data.sources.*`` 选实现 + fixture 模式。

四类数据源（Daily/Market/Enrich/Info）均以 ``default`` 为唯一注册实现；fixture 模式
（``QUANT_USE_LOCAL_FIXTURE``）下同样回退到 ``default``（各 default 实现内部自行判断
是否读离线样本，如 news/index_spot）。
"""

from __future__ import annotations


def fixture_mode() -> bool:
    from common.config import get_settings

    return bool(get_settings().QUANT_USE_LOCAL_FIXTURE)


def _source_name(key: str) -> str:
    """返回 ``data.sources.<key>`` 配置值；fixture 模式或未配置均为 ``default``。"""
    if fixture_mode():
        return "default"
    from quant.config import load_quant_config

    return (load_quant_config().get("data") or {}).get("sources", {}).get(key, "default")


def get_daily_source() -> "DailySource":
    from quant.data.sources.protocols import DailySource  # noqa: F811 (type hint)

    from quant.data.sources.registry import get_daily_source_from_registry

    return get_daily_source_from_registry(_source_name("daily"))


def get_market_source() -> "MarketSource":
    from quant.data.sources.protocols import MarketSource  # noqa: F811 (type hint)

    from quant.data.sources.registry import get_market_source_from_registry

    return get_market_source_from_registry(_source_name("market"))


def get_enrich_source() -> "EnrichSource":
    from quant.data.sources.protocols import EnrichSource  # noqa: F811 (type hint)

    from quant.data.sources.registry import get_enrich_source_from_registry

    return get_enrich_source_from_registry(_source_name("enrich"))


def get_info_source() -> "InfoSource":
    from quant.data.sources.protocols import InfoSource  # noqa: F811 (type hint)

    from quant.data.sources.registry import get_info_source_from_registry

    return get_info_source_from_registry(_source_name("info"))
