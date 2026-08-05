"""AkshareMarketSource：akshare 系运维行情（指数 spot/市场资金流/东财人气·概念板）。

只实现 akshare 支持的接口；未实现抛 NotImplementedError（接口级换源，不隐式 fallback）。
供 ``data.sources.market: {fetch_index_spot: akshare, fetch_market_fund_flow: akshare, ...}``。
"""

from __future__ import annotations

from typing import Any

from fastapi.concurrency import run_in_threadpool

from quant.data.sources.rate_limit import with_limit


class AkshareMarketSource:
    name = "akshare"

    def fetch_index_spot(self) -> Any:
        from common.config import get_settings

        if get_settings().QUANT_USE_LOCAL_FIXTURE:
            from quant.data.sources.fixture.index import FixtureIndexSource

            return FixtureIndexSource().fetch_hs_important()
        from quant.data.sources.akshare.index import AkshareIndexSource

        return AkshareIndexSource().fetch_hs_important()

    async def fetch_market_fund_flow(self, n: int) -> Any:
        from quant.data.sources.akshare.fund_flow import fetch_market_fund_flow_last

        return await run_in_threadpool(fetch_market_fund_flow_last, n)

    def fetch_em_hot_rank(self) -> Any:
        import akshare as ak

        return with_limit("akshare", lambda: ak.stock_hot_rank_em())

    def fetch_em_concept_boards(self) -> Any:
        import akshare as ak

        return with_limit("akshare", lambda: ak.stock_board_concept_name_em())

    # ---- 未实现接口：显式报错 ----
    async def fetch_zqxy(self, *, market_phase: str = "intraday") -> Any:
        raise NotImplementedError("akshare 未实现 fetch_zqxy（可用 ths/default）")

    def fetch_ztgk_pool(self) -> Any:
        raise NotImplementedError("akshare 未实现 fetch_ztgk_pool（可用 eastmoney/default）")

    def fetch_ztgk_prev(self, date: str) -> Any:
        raise NotImplementedError("akshare 未实现 fetch_ztgk_prev（可用 eastmoney/default）")

    async def fetch_hot_raw(self, limit: int) -> Any:
        raise NotImplementedError("akshare 未实现 fetch_hot_raw（可用 ths/default）")

    async def fetch_concept_boards(self) -> Any:
        raise NotImplementedError("akshare 未实现 fetch_concept_boards（可用 ths/default）")

    async def fetch_industry_board(self, context: str, sort_key: str, desc: bool = True) -> Any:
        raise NotImplementedError("akshare 未实现 fetch_industry_board（可用 ths/default）")

    async def fetch_ths_rank_screen(self, kind: str, *, symbol: str | None = None) -> Any:
        raise NotImplementedError("akshare 未实现 fetch_ths_rank_screen（可用 ths/default）")

    def fetch_em_industry_map(self) -> Any:
        raise NotImplementedError("akshare 未实现 fetch_em_industry_map（可用 eastmoney/default）")

    def fetch_industry_map(self) -> Any:
        raise NotImplementedError("akshare 未实现 fetch_industry_map（可用 eastmoney/sina/default）")

    def fetch_stop_resume(self, date: str) -> Any:
        raise NotImplementedError("akshare 未实现 fetch_stop_resume（可用 eastmoney/default）")
