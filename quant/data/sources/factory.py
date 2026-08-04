"""Source factory：返回接口级代理，按 ``quant.yml data.sources.<类>.<接口>`` 独立选源。

facade 一个方法 = 一个接口 = 一个源。业务层 ``get_xxx_source().fetch_yyy()`` 零改动，
内部每个方法按接口路由到配置的源；源未实现某接口抛 NotImplementedError。

配置：
    # 整源（简写）：daily: default
    # 接口级（dict）：daily: {fetch_spot: sina, fetch_hist: tencent, ...}
fixture 模式（QUANT_USE_LOCAL_FIXTURE）下未配置的接口回退 default。
"""

from __future__ import annotations

from typing import Any


def fixture_mode() -> bool:
    from common.config import get_settings

    return bool(get_settings().QUANT_USE_LOCAL_FIXTURE)


def get_daily_source() -> "InterfaceProxy":
    from quant.data.sources.interface import get_interface_proxy
    from quant.data.sources.protocols import DailySource

    return get_interface_proxy("daily", DailySource)


def get_market_source() -> "InterfaceProxy":
    from quant.data.sources.interface import get_interface_proxy
    from quant.data.sources.protocols import MarketSource

    return get_interface_proxy("market", MarketSource)


def get_enrich_source() -> "InterfaceProxy":
    from quant.data.sources.interface import get_interface_proxy
    from quant.data.sources.protocols import EnrichSource

    return get_interface_proxy("enrich", EnrichSource)


def get_info_source() -> "InterfaceProxy":
    from quant.data.sources.interface import get_interface_proxy
    from quant.data.sources.protocols import InfoSource

    return get_interface_proxy("info", InfoSource)


# 兼容旧导出（已废弃，新代码用上面 get_*_source）
def _source_name(key: str) -> str:  # 保留旧签名兼容 import
    from quant.data.sources.interface import _interface_source

    return _interface_source(key, "__old__")  # 实际不再使用
