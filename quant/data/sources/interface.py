"""接口级换源 + 数据驱动 fallback。

facade 一个方法 = 一个接口 = 一个源。fallback 逻辑在业务层（``try_with_fallback``），
不在 facade/proxy 层。

配置（quant.yml ``data.sources.<类>``）：
    # 整源（简写）：daily: default  —— 所有接口走同一实现，不 fallback
    # 接口级（dict）：
    #   单值（不 fallback）：daily: {fetch_spot: sina}
    #   有序列表（fallback）：daily: {fetch_spot: [sina, akshare]}  —— 按序尝试，
    #     失败记日志换下一个，全失败抛异常。

加新源 = 实现源类 + 注册 registry + yml 加到 list，业务层永不改。
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _interface_sources(category: str, iface: str) -> list[str]:
    """返回 ``data.sources.<category>.<iface>`` 的源名列表。

    - 整源字符串 → [该值]（单源，不 fallback）
    - dict 单值 → [该值]（单源，不 fallback）
    - dict list → 该 list（按序 fallback）
    - 缺省 → ["default"]
    """
    from quant.config import load_quant_config

    cfg = (load_quant_config().get("data") or {}).get("sources", {}).get(category, "default")
    if isinstance(cfg, dict):
        val = cfg.get(iface, "default")
    else:
        val = cfg if isinstance(cfg, str) and cfg else "default"
    # str → [str]；list → 去重保序
    if isinstance(val, list):
        seen: set[str] = set()
        out: list[str] = []
        for s in val:
            s = str(s).strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out or ["default"]
    return [str(val).strip() or "default"]


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


def try_with_fallback(category: str, iface: str, **kwargs) -> Any:
    """业务层通用 fallback：按 yml 配置的源列表逐个尝试。

    单源（str）= 只试一个，失败抛异常（不 fallback）。
    多源（list）= 按序尝试，失败记日志换下一个，全失败抛 RuntimeError。

    用法::

        from quant.data.sources.interface import try_with_fallback
        spot = try_with_fallback("daily", "fetch_spot")
        idx = try_with_fallback("daily", "fetch_index", code="000300", start="2026-01-01", end="2026-08-04")
    """
    sources = _interface_sources(category, iface)
    last_exc: Exception | None = None
    for src_name in sources:
        try:
            src = _make_source(category, src_name)
            fn = getattr(src, iface, None)
            if fn is None:
                raise NotImplementedError(f"源 '{src_name}' 未实现 {category}.{iface}")
            return fn(**kwargs)
        except NotImplementedError:
            raise  # 源未实现该接口，不 fallback，直接抛（配置错误）
        except Exception as e:
            last_exc = e
            if len(sources) > 1:
                logger.warning("%s.%s 源 '%s' 失败: %s → 试下一个", category, iface, src_name, e)
    # 全失败
    if len(sources) == 1:
        raise last_exc  # 单源模式：直接抛，不包 RuntimeError
    raise RuntimeError(
        f"{category}.{iface}: 全部源 {sources} 均失败（最后错误: {last_exc}）"
    ) from last_exc


async def try_with_fallback_async(category: str, iface: str, **kwargs) -> Any:
    """``try_with_fallback`` 的 async 版（enrich/market 的 async 接口用）。"""
    import asyncio

    sources = _interface_sources(category, iface)
    last_exc: Exception | None = None
    for src_name in sources:
        try:
            src = _make_source(category, src_name)
            fn = getattr(src, iface, None)
            if fn is None:
                raise NotImplementedError(f"源 '{src_name}' 未实现 {category}.{iface}")
            result = fn(**kwargs)
            if asyncio.iscoroutine(result):
                return await result
            return result
        except NotImplementedError:
            raise
        except Exception as e:
            last_exc = e
            if len(sources) > 1:
                logger.warning("%s.%s 源 '%s' 失败: %s → 试下一个", category, iface, src_name, e)
    if len(sources) == 1:
        raise last_exc
    raise RuntimeError(
        f"{category}.{iface}: 全部源 {sources} 均失败（最后错误: {last_exc}）"
    ) from last_exc


# ---- 兼容：InterfaceProxy 仍可用（单源模式），但推荐 try_with_fallback ----

def _interface_source(category: str, iface: str) -> str:
    """单源名（兼容旧 InterfaceProxy）。"""
    sources = _interface_sources(category, iface)
    return sources[0]


class InterfaceProxy:
    """按接口路由到配置源的代理（单源模式，无 fallback）。

    推荐用 ``try_with_fallback`` 替代（支持多源 fallback）。
    """

    def __init__(self, category: str, ifaces: list[str]) -> None:
        self._category = category
        self._ifaces = ifaces

    @property
    def name(self) -> str:
        return _interface_source(self._category, "__default__")

    def __getattr__(self, name: str) -> Any:
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
        return fn.__get__(src, type(src))


def get_interface_proxy(category: str, protocol_cls) -> InterfaceProxy:
    ifaces = [
        m for m, _ in inspect.getmembers(protocol_cls, predicate=inspect.isfunction)
        if m.startswith("fetch_")
    ]
    return InterfaceProxy(category, ifaces)
