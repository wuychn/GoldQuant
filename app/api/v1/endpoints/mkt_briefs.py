"""财经快讯、早餐、多源全球资讯、个股新闻。"""

from __future__ import annotations

import akshare as ak
from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.schemas.ak_openapi import (
    EmNewsIn,
    EmNewsOut,
    field_desc,
)
from app.schemas.ak_table import AkTableOut
from app.schemas.response import Response
from app.utils.ak_response import wrap_ak_dataframe, wrap_ak_table

_DOC = "https://akshare.akfamily.xyz/data/stock/stock.html"
router = APIRouter(tags=["资讯", "快讯"])


def _ak(name: str, params: dict, df) -> AkTableOut:
    return wrap_ak_table(name, params, df)


@router.get(
    "/news/em",
    response_model=Response,
    summary="单只股票资讯流",
    description=(
        "封装 `ak.stock_news_em`。\n\n"
        f"文档：[个股新闻]({_DOC}#个股新闻)。"
    ),
)
async def em_stock_news(
    symbol: str = Query(
        ...,
        description=field_desc(EmNewsIn, "symbol"),
        examples=["603777"],
    ),
) -> Response:
    try:
        df = await run_in_threadpool(ak.stock_news_em, symbol)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(
        data=wrap_ak_dataframe(
            EmNewsOut, "stock_news_em", EmNewsIn(symbol=symbol), df
        )
    )


@router.get(
    "/stock/em/info/cjzc",
    response_model=Response,
    summary="财经早餐",
    description=f"封装 `ak.stock_info_cjzc_em`，无入参。文档：[A 股数据]({_DOC})。",
)
async def em_info_cjzc() -> Response:
    try:
        df = await run_in_threadpool(ak.stock_info_cjzc_em)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_info_cjzc_em", {}, df))


@router.get(
    "/stock/em/info/global",
    response_model=Response,
    summary="全球财经快讯",
    description="封装 `ak.stock_info_global_em`，无入参。",
)
async def em_info_global() -> Response:
    try:
        df = await run_in_threadpool(ak.stock_info_global_em)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_info_global_em", {}, df))


@router.get(
    "/stock/sina/info/global",
    response_model=Response,
    summary="全球财经快讯（新浪源）",
    description="封装 `ak.stock_info_global_sina`，无入参。",
)
async def sina_info_global() -> Response:
    try:
        df = await run_in_threadpool(ak.stock_info_global_sina)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_info_global_sina", {}, df))


@router.get(
    "/stock/futu/info/global",
    response_model=Response,
    summary="快讯",
    description="封装 `ak.stock_info_global_futu`，无入参。",
)
async def futu_info_global() -> Response:
    try:
        df = await run_in_threadpool(ak.stock_info_global_futu)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_info_global_futu", {}, df))


@router.get(
    "/stock/cls/telegraph",
    response_model=Response,
    summary="财联社·电报",
    description="封装 `ak.stock_info_global_cls`；`symbol` 如 全部/重点 等与官方一致。",
)
async def cls_telegraph(
    symbol: str = Query("全部", description="频道或范围。例 全部。"),
) -> Response:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_info_global_cls(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_info_global_cls", p, df))
