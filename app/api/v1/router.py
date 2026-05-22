"""聚合 v1 路由（按业务类别组织，文件名为功能域，不绑定数据源）。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import (
    mkt_block,
    mkt_board,
    mkt_briefs,
    mkt_config,
    mkt_corporate,
    mkt_dealer,
    mkt_disclosure,
    mkt_extremes,
    mkt_funds,
    mkt_heat,
    mkt_interconnect,
    mkt_margin,
    mkt_quotes,
    mkt_research,
    mkt_screens,
    mkt_sentiment, quant_endpoint,
)

api_router = APIRouter()
api_router.include_router(mkt_heat.router)
api_router.include_router(mkt_briefs.router)
api_router.include_router(mkt_quotes.router)
api_router.include_router(mkt_funds.router)
api_router.include_router(mkt_dealer.router)
api_router.include_router(mkt_board.router)
api_router.include_router(mkt_extremes.router)
api_router.include_router(mkt_sentiment.router)
api_router.include_router(mkt_screens.router)
api_router.include_router(mkt_corporate.router)
api_router.include_router(mkt_research.router)
api_router.include_router(mkt_interconnect.router)
api_router.include_router(mkt_disclosure.router)
api_router.include_router(mkt_block.router)
api_router.include_router(mkt_margin.router)
api_router.include_router(mkt_config.router)
api_router.include_router(quant_endpoint.router)
