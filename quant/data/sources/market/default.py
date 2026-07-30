"""DefaultMarketSource：运维行情组合最优（推荐默认）。

各接口实测最稳源不同，故默认实现是组合：
- index_spot：沪深重要指数（fixture 模式读离线样本，否则 akshare stock_zh_index_spot_em）
- zqxy：ths V2 → 52etf → ths（三级 fallback）
- ztgk_pool/ztgk_prev：东财 ztgc/ztgc_with_date
- hot/concept/industry：ths（httpx + Hexin-V）
- market_fund_flow：akshare fund_flow

业务编排（过滤/高度/prefilter/解包）留在 ``tools/market.py`` facade；本类只取数。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi.concurrency import run_in_threadpool

from quant.data.sources.rate_limit import alimit

logger = logging.getLogger(__name__)


class DefaultMarketSource:
    name = "default"

    def fetch_index_spot(self) -> Any:
        # 保留旧 IndexSource 的 fixture/akswitch 语义（原 get_index_source()）：
        # fixture 模式读 index_spot.json 离线样本，否则走 akshare 沪深重要指数。
        from common.config import get_settings

        if get_settings().QUANT_USE_LOCAL_FIXTURE:
            from quant.data.sources.fixture.index import FixtureIndexSource

            return FixtureIndexSource().fetch_hs_important()
        from quant.data.sources.akshare.index import AkshareIndexSource

        return AkshareIndexSource().fetch_hs_important()

    async def fetch_zqxy(self, *, market_phase: str = "intraday") -> Any:
        from quant.data.sources.etf52.zdfb import zdfb_52etf
        from quant.data.sources.ths import zdfb_ths, zdfb_v2_realtime

        for coro_factory, label in (
            (lambda: alimit("ths", lambda: zdfb_v2_realtime()), "赚钱效应 | 同花顺V2接口"),
            (lambda: zdfb_52etf(market_phase=market_phase), "赚钱效应 | 52etf涨跌分布"),
            (lambda: alimit("ths", lambda: zdfb_ths()), "赚钱效应 | 同花顺涨跌分布"),
        ):
            try:
                return await coro_factory()
            except Exception:
                logger.warning("量化数据 [%s]", label)
        return None

    def fetch_ztgk_pool(self) -> Any:
        from quant.data.sources.eastmoney import ztgc

        return ztgc()

    def fetch_ztgk_prev(self, date: str) -> Any:
        from quant.data.sources.eastmoney import ztgc_with_date

        return ztgc_with_date(date)

    async def fetch_hot_raw(self, limit: int) -> Any:
        from quant.data.sources.ths import hot_stock

        return await alimit("ths", lambda: hot_stock(limit))

    async def fetch_concept_boards(self) -> Any:
        from quant.data.sources.ths import concept_board_top_lists

        return await alimit("ths", lambda: concept_board_top_lists("即时"))

    async def fetch_industry_board(self, context: str, sort_key: str, desc: bool = True) -> Any:
        from quant.data.sources.ths import hyylb

        return await alimit("ths", lambda: hyylb(sort_key, desc))

    async def fetch_market_fund_flow(self, n: int) -> Any:
        from quant.data.sources.akshare.fund_flow import fetch_market_fund_flow_last

        return await run_in_threadpool(fetch_market_fund_flow_last, n)

    async def fetch_ths_rank_screen(self, kind: str, *, symbol: str | None = None) -> Any:
        """同花顺形态榜（创新高/持续上涨/持续放量/量价齐升）。

        ``kind`` ∈ {cxg, lxsz, cxfl, ljqs}；仅 ``cxg`` 用 ``symbol``（标签，默认"创月新高"）。
        供候选池 ``pool/candidate_sources`` 初筛。
        """
        from quant.data.sources.ths import cxfl, cxg, ljqs, lxsz

        if kind == "cxg":
            return await alimit("ths", lambda: cxg(symbol or "创月新高"))
        fn = {"lxsz": lxsz, "cxfl": cxfl, "ljqs": ljqs}.get(kind)
        if fn is None:
            raise ValueError(f"未知 ths 形态榜 kind={kind}")
        return await alimit("ths", lambda: fn())

    def fetch_em_industry_map(self) -> Any:
        """东财全量个股行业映射（code→行业，供行业库 PIT 落库）。"""
        from quant.data.sources.eastmoney.industry import fetch_em_industry_board

        return fetch_em_industry_board()

    def fetch_em_hot_rank(self) -> Any:
        """东财人气榜全市场（``ak.stock_hot_rank_em``；区别于同花顺 ``fetch_hot_raw``）。"""
        import akshare as ak

        return ak.stock_hot_rank_em()

    def fetch_em_concept_boards(self) -> Any:
        """东财概念板块列表（``ak.stock_board_concept_name_em``，含涨跌幅）。"""
        import akshare as ak

        return ak.stock_board_concept_name_em()
