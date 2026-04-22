"""东财：盘口/板块异动与涨跌停、次新股等股池。"""

from __future__ import annotations

import akshare as ak
from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.schemas.ak_table import AkTableOut
from app.utils.ak_response import wrap_ak_table

_DOC = "https://akshare.akfamily.xyz/data/stock/stock.html"
router = APIRouter(tags=["异动", "涨跌停", "东财", "股池"])


def _ak(name: str, params: dict, df) -> AkTableOut:
    return wrap_ak_table(name, params, df)


@router.get(
    "/stock/em/changes",
    response_model=AkTableOut,
    summary="盘口异动（东财）",
    description=f"封装 `ak.stock_changes_em`；`symbol` 为异动类型。文档：[A 股数据]({_DOC})。",
)
async def em_stock_changes(
    symbol: str = Query("大笔买入", description="异动类型名。例 大笔买入。"),
) -> AkTableOut:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_changes_em(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_changes_em", p, df)


@router.get(
    "/stock/em/board-change",
    response_model=AkTableOut,
    summary="板块异动详情（东财）",
    description="封装 `ak.stock_board_change_em`，无入参。",
)
async def em_board_change() -> AkTableOut:
    try:
        df = await run_in_threadpool(ak.stock_board_change_em)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_board_change_em", {}, df)


@router.get(
    "/stock/em/zt-pool",
    response_model=AkTableOut,
    summary="涨停板池（东财）",
    description="封装 `ak.stock_zt_pool_em`；`date` 为交易日 YYYYMMDD。",
)
async def em_zt_pool(
    date: str = Query("20241008", description="交易日期，YYYYMMDD。"),
) -> AkTableOut:
    p = {"date": date}
    try:
        df = await run_in_threadpool(lambda: ak.stock_zt_pool_em(date=date))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_zt_pool_em", p, df)


@router.get(
    "/stock/em/zt-pool/previous",
    response_model=AkTableOut,
    summary="昨日涨停股池（东财）",
    description="封装 `ak.stock_zt_pool_previous_em`；`date` 为 YYYYMMDD。",
)
async def em_zt_pool_previous(
    date: str = Query("20240415", description="交易日期，YYYYMMDD。"),
) -> AkTableOut:
    p = {"date": date}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_zt_pool_previous_em(date=date)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_zt_pool_previous_em", p, df)


@router.get(
    "/stock/em/zt-pool/strong",
    response_model=AkTableOut,
    summary="强势股池（东财）",
    description="封装 `ak.stock_zt_pool_strong_em`；`date` 为 YYYYMMDD。",
)
async def em_zt_pool_strong(
    date: str = Query("20241231", description="交易日期，YYYYMMDD。"),
) -> AkTableOut:
    p = {"date": date}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_zt_pool_strong_em(date=date)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_zt_pool_strong_em", p, df)


@router.get(
    "/stock/em/zt-pool/sub-new",
    response_model=AkTableOut,
    summary="次新股池（东财）",
    description="封装 `ak.stock_zt_pool_sub_new_em`；`date` 为 YYYYMMDD。",
)
async def em_zt_pool_sub_new(
    date: str = Query("20241231", description="交易日期，YYYYMMDD。"),
) -> AkTableOut:
    p = {"date": date}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_zt_pool_sub_new_em(date=date)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_zt_pool_sub_new_em", p, df)


@router.get(
    "/stock/em/zt-pool/zbgc",
    response_model=AkTableOut,
    summary="炸板股池（东财）",
    description="封装 `ak.stock_zt_pool_zbgc_em`；`date` 为 YYYYMMDD。",
)
async def em_zt_pool_zbgc(
    date: str = Query("20241011", description="交易日期，YYYYMMDD。"),
) -> AkTableOut:
    p = {"date": date}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_zt_pool_zbgc_em(date=date)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_zt_pool_zbgc_em", p, df)


@router.get(
    "/stock/em/zt-pool/dtgc",
    response_model=AkTableOut,
    summary="跌停股池（东财）",
    description="封装 `ak.stock_zt_pool_dtgc_em`；`date` 为 YYYYMMDD。",
)
async def em_zt_pool_dtgc(
    date: str = Query("20241011", description="交易日期，YYYYMMDD。"),
) -> AkTableOut:
    p = {"date": date}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_zt_pool_dtgc_em(date=date)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_zt_pool_dtgc_em", p, df)
