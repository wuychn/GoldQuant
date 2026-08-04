"""接口级换源代理：facade 一个方法 = 一个接口 = 一个源。

``get_*_source()`` 返回一个 ``InterfaceProxy``，业务层 ``src.fetch_xxx()`` 照常调用；
内部每个方法按 ``data.sources.<类>.<方法名>`` 独立选源，源未实现则抛 NotImplementedError。

配置（quant.yml）：
    # 整源（简写）：daily: default —— 所有接口走同一实现
    # 接口级（dict）：daily: {fetch_spot: sina, fetch_hist: tencent, ...}
    #   —— 每个接口独立选源；未配置的接口缺省 default；源未实现某接口抛 NotImplementedError。
"""

from __future__ import annotations

import inspect
from typing import Any


def _interface_source(category: str, iface: str) -> str:
    """返回 ``data.sources.<category>.<iface>`` 源名；整源字符串/dict/缺省 → 解析。"""
    from quant.config import load_quant_config

    cfg = (load_quant_config().get("data") or {}).get("sources", {}).get(category, "default")
    if isinstance(cfg, dict):
        return cfg.get(iface, "default")
    return cfg if isinstance(cfg, str) and cfg else "default"


def _make_source(category: str, name: str) -> Any:
    """按 category+name 实例化 registry 中的源实现。"""
    if category == "daily":
        from quant.data.sources.registry import get_daily_source_from_registry
        return get_daily_source_from_registry(name)
    if category == "market":
        from quant.data.sources.registry import get_market_source_from_registry
        return get_market_source_from_registry(name)
    if category == "enrich":
        from quant.data.sources.registry import get_enrich_source_from_registry
        return get_enrich_source_from_registry(name)
    if category == "info":
        from quant.data.sources.registry import get_info_source_from_registry
        return get_info_source_from_registry(name)
    raise ValueError(f"未知 source category: {category}")


class InterfaceProxy:
    """按接口路由到配置源的代理。业务层 ``src.fetch_xxx()`` 零改动。"""

    def __init__(self, category: str, ifaces: list[str]) -> None:
        self._category = category
        self._ifaces = ifaces

    @property
    def name(self) -> str:
        """整源配置名（兼容旧断言 get_xxx_source().name）。"""
        return _interface_source(self._category, "__default__")

    def __getattr__(self, name: str) -> Any:
        # 只代理协议接口（fetch_*），其余属性走实例默认
        if not name.startswith("fetch_"):
            raise AttributeError(name)
        src_name = _interface_source(self._category, name)
        src = _make_source(self._category, src_name)
        fn = getattr(src, name, None)
        if fn is None:
            raise NotImplementedError(
                f"{self._category}.{name}: 源 '{src_name}' 未实现该接口，"
                f"请改配置 data.sources.{self._category}.{name}"
            )
        # 绑定源实例，保留原签名（含 async）
        return fn.__get__(src, type(src))


def get_interface_proxy(category: str, protocol_cls) -> InterfaceProxy:
    """返回某协议类的接口级代理。"""
    ifaces = [
        m for m, _ in inspect.getmembers(protocol_cls, predicate=inspect.isfunction)
        if m.startswith("fetch_")
    ]
    return InterfaceProxy(category, ifaces)
