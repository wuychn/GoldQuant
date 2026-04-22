"""mkt_margin"""
from __future__ import annotations

import akshare as ak
from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.schemas.ak_table import AkTableOut
from app.utils.ak_response import wrap_ak_table

router = APIRouter(tags=['融资融券'])


def _ak(name: str, params: dict, df) -> AkTableOut:
    return wrap_ak_table(name, params, df)


@router.get(
    "/stock/em/margin/account-info",
    response_model=AkTableOut,
    summary="两融账户信息（东财）",
    description="封装 `ak.stock_margin_account_info`，无入参。",
)
async def em_margin_account_info() -> AkTableOut:
    try:
        df = await run_in_threadpool(ak.stock_margin_account_info)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_margin_account_info", {}, df)


@router.get(
    "/stock/em/margin/sse",
    response_model=AkTableOut,
    summary="融资融券汇总（上证）",
    description="封装 `ak.stock_margin_sse`；上交所汇总。",
)
async def em_margin_sse(
    start_date: str = Query("20010106", description="开始 YYYYMMDD。"),
    end_date: str = Query("20210208", description="结束 YYYYMMDD。"),
) -> AkTableOut:
    p = {"start_date": start_date, "end_date": end_date}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_margin_sse(
                start_date=start_date, end_date=end_date
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_margin_sse", p, df)


@router.get(
    "/stock/em/margin/detail-sse",
    response_model=AkTableOut,
    summary="融资融券明细（上证）",
    description="封装 `ak.stock_margin_detail_sse`。",
)
async def em_margin_detail_sse(
    date: str = Query("20230922", description="交易日 YYYYMMDD。"),
) -> AkTableOut:
    p = {"date": date}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_margin_detail_sse(date=date)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_margin_detail_sse", p, df)


@router.get(
    "/stock/em/margin/szse",
    response_model=AkTableOut,
    summary="融资融券汇总（深证）",
    description="封装 `ak.stock_margin_szse`；`date` 为单日。",
)
async def em_margin_szse(
    date: str = Query("20240411", description="交易日 YYYYMMDD。"),
) -> AkTableOut:
    p = {"date": date}
    try:
        df = await run_in_threadpool(lambda: ak.stock_margin_szse(date=date))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_margin_szse", p, df)


@router.get(
    "/stock/em/margin/detail-szse",
    response_model=AkTableOut,
    summary="融资融券明细（深证）",
    description="封装 `ak.stock_margin_detail_szse`。",
)
async def em_margin_detail_szse(
    date: str = Query("20230925", description="交易日 YYYYMMDD。"),
) -> AkTableOut:
    p = {"date": date}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_margin_detail_szse(date=date)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_margin_detail_szse", p, df)

