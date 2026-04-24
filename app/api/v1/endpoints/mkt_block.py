"""大宗交易统计与明细。"""
from __future__ import annotations

import akshare as ak
from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.schemas.ak_table import AkTableOut
from app.schemas.response import Response
from app.utils.ak_response import wrap_ak_table

router = APIRouter(tags=['大宗交易'])


def _ak(name: str, params: dict, df) -> AkTableOut:
    return wrap_ak_table(name, params, df)


@router.get(
    "/stock/em/dzjy/hygtj",
    response_model=Response,
    summary="活跃A股统计（东财·大宗）",
    description="封装 `ak.stock_dzjy_hygtj`；`symbol` 为统计期描述。",
)
async def em_dzjy_hygtj(
    symbol: str = Query("近三月", description="如 近三月/近一年 等。"),
) -> Response:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_dzjy_hygtj(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_dzjy_hygtj", p, df))


@router.get(
    "/stock/em/dzjy/hyyybtj",
    response_model=Response,
    summary="活跃营业部统计（东财·大宗）",
    description="封装 `ak.stock_dzjy_hyyybtj`；`symbol` 为统计期。",
)
async def em_dzjy_hyyybtj(
    symbol: str = Query("近3日", description="如 近3日/近1月 等。与官方一致。"),
) -> Response:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_dzjy_hyyybtj(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_dzjy_hyyybtj", p, df))


@router.get(
    "/stock/em/dzjy/yybph",
    response_model=Response,
    summary="营业部排行（东财·大宗）",
    description="封装 `ak.stock_dzjy_yybph`。",
)
async def em_dzjy_yybph(
    symbol: str = Query("近三月", description="统计期，如 近三月。"),
) -> Response:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_dzjy_yybph(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_dzjy_yybph", p, df))

