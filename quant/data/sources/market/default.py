"""DefaultMarketSource：组合源（每个方法委托到最优的单源实现）。

接口级：facade 一个方法 = 一个接口 = 一个源。
- fetch_index_spot：akshare（AkshareMarketSource，fixture 模式读样本）
- fetch_zqxy：同花顺（ThsMarketSource，V2→52etf→ths 三级）
- fetch_ztgk_pool/prev：东财（EastmoneyMarketSource）
- fetch_hot_raw/concept_boards/industry_board/ths_rank_screen：同花顺
- fetch_market_fund_flow：akshare
- fetch_em_industry_map：东财
- fetch_em_hot_rank/concept_boards：akshare
"""

from __future__ import annotations

from typing import Any

from quant.data.sources.market.akshare import AkshareMarketSource
from quant.data.sources.market.eastmoney import EastmoneyMarketSource
from quant.data.sources.market.ths import ThsMarketSource


class DefaultMarketSource:
    name = "default"

    def __init__(self) -> None:
        self._ak = AkshareMarketSource()
        self._em = EastmoneyMarketSource()
        self._ths = ThsMarketSource()

    def fetch_index_spot(self) -> Any:
        return self._ak.fetch_index_spot()

    async def fetch_zqxy(self, *, market_phase: str = "intraday") -> Any:
        return await self._ths.fetch_zqxy(market_phase=market_phase)

    def fetch_ztgk_pool(self) -> Any:
        return self._em.fetch_ztgk_pool()

    def fetch_ztgk_prev(self, date: str) -> Any:
        return self._em.fetch_ztgk_prev(date)

    async def fetch_hot_raw(self, limit: int) -> Any:
        return await self._ths.fetch_hot_raw(limit)

    async def fetch_concept_boards(self) -> Any:
        return await self._ths.fetch_concept_boards()

    async def fetch_industry_board(self, context: str, sort_key: str, desc: bool = True) -> Any:
        return await self._ths.fetch_industry_board(context, sort_key, desc)

    async def fetch_market_fund_flow(self, n: int) -> Any:
        return await self._ak.fetch_market_fund_flow(n)

    async def fetch_ths_rank_screen(self, kind: str, *, symbol: str | None = None) -> Any:
        return await self._ths.fetch_ths_rank_screen(kind, symbol=symbol)

    def fetch_em_industry_map(self) -> Any:
        return self._em.fetch_em_industry_map()

    def fetch_em_hot_rank(self) -> Any:
        return self._ak.fetch_em_hot_rank()

    def fetch_em_concept_boards(self) -> Any:
        return self._ak.fetch_em_concept_boards()
