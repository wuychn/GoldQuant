"""热度、人气与个股资讯（东财）。"""

from __future__ import annotations

import akshare as ak
from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.schemas.ak_openapi import (
    EmNewsIn,
    EmNewsOut,
    EmPopularityOut,
    EmSurgeOut,
    field_desc,
)
from app.utils.ak_response import wrap_ak_dataframe

router = APIRouter(tags=["热度", "人气", "资讯"])

_BASE = "https://akshare.akfamily.xyz/data/stock/stock.html"


@router.get(
    "/hot/eastmoney/surge",
    response_model=EmSurgeOut,
    summary="东方财富飙升榜（A股）",
    description=(
        "封装 `ak.stock_hot_up_em`（个股人气榜-飙升榜）。\n\n"
        f"文档：[飙升榜-A股]({_BASE}#飙升榜-a股)。\n"
        "入参、出参结构见本页 **Parameters** 与 **Responses** 中 Schema。"
    ),
)
async def eastmoney_surge() -> EmSurgeOut:
    try:
        df = await run_in_threadpool(ak.stock_hot_up_em)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return wrap_ak_dataframe(EmSurgeOut, "stock_hot_up_em", {}, df)


@router.get(
    "/hot/eastmoney/popularity",
    response_model=EmPopularityOut,
    summary="东方财富个股人气榜（A股，前100）",
    description=(
        "封装 `ak.stock_hot_rank_em`（人气榜-A股）。\n\n"
        f"文档：[人气榜-A股]({_BASE}#人气榜-a股)。"
    ),
)
async def eastmoney_popularity() -> EmPopularityOut:
    try:
        df = await run_in_threadpool(ak.stock_hot_rank_em)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return wrap_ak_dataframe(EmPopularityOut, "stock_hot_rank_em", {}, df)


@router.get(
    "/news/em",
    response_model=EmNewsOut,
    summary="东方财富个股资讯",
    description=(
        "封装 `ak.stock_news_em`。\n\n"
        f"文档：[个股新闻]({_BASE}#个股新闻)。"
    ),
)
async def eastmoney_news(
    symbol: str = Query(
        ...,
        description=field_desc(EmNewsIn, "symbol"),
        examples=["603777"],
    ),
) -> EmNewsOut:
    try:
        df = await run_in_threadpool(ak.stock_news_em, symbol)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return wrap_ak_dataframe(EmNewsOut, "stock_news_em", EmNewsIn(symbol=symbol), df)
