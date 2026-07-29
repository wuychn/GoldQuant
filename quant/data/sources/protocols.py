"""Source Protocol definitions for market data."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SpotSource(Protocol):
    def fetch_all(self) -> list[dict[str, Any]]: ...


@runtime_checkable
class IndexSource(Protocol):
    def fetch_hs_important(self) -> list[dict[str, Any]]: ...


@runtime_checkable
class NewsSource(Protocol):
    def fetch_global(self) -> list[dict[str, Any]]: ...
