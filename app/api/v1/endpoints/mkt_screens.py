"""技术形态、强弱、连板与创新高类榜单/筛股。"""

from __future__ import annotations

import akshare as ak
from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.schemas.ak_table import AkTableOut
from app.schemas.response import Response
from app.utils.ak_response import wrap_ak_table

router = APIRouter(tags=["排行", "技术形态", "连板", "榜"])


def _ak(name: str, params: dict, df) -> AkTableOut:
    return wrap_ak_table(name, params, df)


@router.get(
    "/stock/ths/rank/cxg",
    response_model=Response,
    summary="创新高（同花顺）",
    description="封装 `ak.stock_rank_cxg_ths`；`symbol` 为周期档位名，如 创月新高。",
)
async def ths_rank_cxg(
    symbol: str = Query("创月新高", description="榜单类型。例 创月新高。"),
) -> Response:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_rank_cxg_ths(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_rank_cxg_ths", p, df))


@router.get(
    "/stock/ths/rank/cxd",
    response_model=Response,
    summary="创新低（同花顺）",
    description="封装 `ak.stock_rank_cxd_ths`；`symbol` 如 创月新低。",
)
async def ths_rank_cxd(
    symbol: str = Query("创月新低", description="榜单类型。例 创月新低。"),
) -> Response:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_rank_cxd_ths(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_rank_cxd_ths", p, df))


@router.get(
    "/stock/ths/rank/lxsz",
    response_model=Response,
    summary="连续上涨（同花顺）",
    description="封装 `ak.stock_rank_lxsz_ths`，无入参。",
)
async def ths_rank_lxsz() -> Response:
    try:
        df = await run_in_threadpool(ak.stock_rank_lxsz_ths)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_rank_lxsz_ths", {}, df))


@router.get(
    "/stock/ths/rank/lxxd",
    response_model=Response,
    summary="连续下跌（同花顺）",
    description="封装 `ak.stock_rank_lxxd_ths`，无入参。",
)
async def ths_rank_lxxd() -> Response:
    try:
        df = await run_in_threadpool(ak.stock_rank_lxxd_ths)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_rank_lxxd_ths", {}, df))


@router.get(
    "/stock/ths/rank/cxfl",
    response_model=Response,
    summary="持续放量（同花顺）",
    description="封装 `ak.stock_rank_cxfl_ths`，无入参。",
)
async def ths_rank_cxfl() -> Response:
    try:
        df = await run_in_threadpool(ak.stock_rank_cxfl_ths)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_rank_cxfl_ths", {}, df))


@router.get(
    "/stock/ths/rank/cxsl",
    response_model=Response,
    summary="持续缩量（同花顺）",
    description="封装 `ak.stock_rank_cxsl_ths`，无入参。",
)
async def ths_rank_cxsl() -> Response:
    try:
        df = await run_in_threadpool(ak.stock_rank_cxsl_ths)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_rank_cxsl_ths", {}, df))
