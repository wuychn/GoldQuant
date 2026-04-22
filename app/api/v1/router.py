"""聚合 v1 路由（按业务类别组织：热度、行情、资金、龙虎、概念、龙虎/评级等）。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import (
    eastmoney_config,
    mkt_block_trade,
    mkt_company_social,
    mkt_concept_board,
    mkt_disclosure,
    mkt_em_abnormal_zt,
    mkt_fund_flow,
    mkt_hsgt,
    mkt_hot_popularity,
    mkt_info_feeds,
    mkt_legu_sentiment,
    mkt_lhb,
    mkt_margin,
    mkt_quotes_kline,
    mkt_research_ratings,
    mkt_ths_portal,
    mkt_ths_rankings,
)

api_router = APIRouter()
api_router.include_router(mkt_hot_popularity.router)
api_router.include_router(mkt_quotes_kline.router)
api_router.include_router(mkt_fund_flow.router)
api_router.include_router(mkt_lhb.router)
api_router.include_router(mkt_concept_board.router)
api_router.include_router(mkt_em_abnormal_zt.router)
api_router.include_router(mkt_info_feeds.router)
api_router.include_router(mkt_legu_sentiment.router)
api_router.include_router(mkt_ths_rankings.router)
api_router.include_router(mkt_company_social.router)
api_router.include_router(mkt_research_ratings.router)
api_router.include_router(mkt_ths_portal.router)
api_router.include_router(mkt_hsgt.router)
api_router.include_router(mkt_disclosure.router)
api_router.include_router(mkt_block_trade.router)
api_router.include_router(mkt_margin.router)
api_router.include_router(eastmoney_config.router)
