"""机构推荐与投资评级（新浪/巨潮源）。"""

from __future__ import annotations

import akshare as ak
from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.schemas.ak_table import AkTableOut
from app.schemas.response import Response
from app.utils.ak_response import wrap_ak_table

router = APIRouter(tags=["机构", "评级"])


def _ak(name: str, params: dict, df) -> AkTableOut:
    return wrap_ak_table(name, params, df)


@router.get(
    "/stock/sina/institute-recommend",
    response_model=Response,
    summary="机构推荐池（新浪）",
    description="封装 `ak.stock_institute_recommend`；`symbol` 为池类型名。",
)
async def sina_institute_recommend(
    symbol: str = Query("投资评级选股", description="池类型/名称。例 投资评级选股 等。与官方一致。"),
) -> Response:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_institute_recommend(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_institute_recommend", p, df))


@router.get(
    "/stock/sina/institute-recommend-detail",
    response_model=Response,
    summary="股票评级记录（新浪）",
    description="封装 `ak.stock_institute_recommend_detail`；`symbol` 为 6 位股票代码。",
)
async def sina_institute_recommend_detail(
    symbol: str = Query("002709", description="股票代码。例 002709。"),
) -> Response:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_institute_recommend_detail(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_institute_recommend_detail", p, df))


@router.get(
    "/stock/sina/rank-forecast-cninfo",
    response_model=Response,
    summary="投资评级-巨潮（新浪/巨潮源）",
    description="封装 `ak.stock_rank_forecast_cninfo`；`date` 为 YYYYMMDD。",
)
async def sina_rank_forecast_cninfo(
    date: str = Query("20230817", description="日期 YYYYMMDD。例 20230817。"),
) -> Response:
    p = {"date": date}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_rank_forecast_cninfo(date=date)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_rank_forecast_cninfo", p, df))
