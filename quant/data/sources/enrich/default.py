"""DefaultEnrichSource：组合源（每个方法委托到最优的单源实现）。

组合原则（接口级）：facade 一个方法 = 一个接口 = 一个源。default 是"每个接口各自
委托最稳的单源"；需要逐接口自定义时用接口级配置（``data.sources.enrich`` dict）。

- fetch_stock_quote：东财（EastmoneyEnrichSource）
- fetch_stock_fund_flow：同花顺（ThsEnrichSource）
- fetch_stock_fund_flow_daily：东财（EastmoneyEnrichSource）
- fetch_concept_fit_rank：同花顺（ThsEnrichSource）
- fetch_stock_concepts：问财（WencaiEnrichSource）
- fetch_stock_minute：akshare（AkshareEnrichSource）
"""

from __future__ import annotations

from typing import Any

from quant.data.sources.enrich.akshare import AkshareEnrichSource
from quant.data.sources.enrich.eastmoney import EastmoneyEnrichSource
from quant.data.sources.enrich.ths import ThsEnrichSource
from quant.data.sources.enrich.wencai import WencaiEnrichSource


class DefaultEnrichSource:
    name = "default"

    def __init__(self) -> None:
        self._em = EastmoneyEnrichSource()
        self._ths = ThsEnrichSource()
        self._wencai = WencaiEnrichSource()
        self._ak = AkshareEnrichSource()

    async def fetch_stock_quote(self, symbol: str) -> dict | None:
        return await self._em.fetch_stock_quote(symbol)

    async def fetch_stock_fund_flow(self, symbol: str) -> dict | None:
        return await self._ths.fetch_stock_fund_flow(symbol)

    async def fetch_stock_fund_flow_daily(self, symbol: str, *, days: int = 10) -> list[dict] | None:
        return await self._em.fetch_stock_fund_flow_daily(symbol, days=days)

    async def fetch_concept_fit_rank(self, symbol: str) -> list[dict[str, Any]]:
        return await self._ths.fetch_concept_fit_rank(symbol)

    async def fetch_stock_concepts(self, symbol: str, *, name: str | None = None) -> list[str]:
        return await self._wencai.fetch_stock_concepts(symbol, name=name)

    async def fetch_stock_minute(self, symbol: str, *, context: str = "") -> list[dict[str, Any]] | None:
        return await self._ak.fetch_stock_minute(symbol, context=context)
