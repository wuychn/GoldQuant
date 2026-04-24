"""mkt_disclosure"""
from __future__ import annotations

import akshare as ak
from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.schemas.ak_table import AkTableOut
from app.schemas.response import Response
from app.utils.ak_response import wrap_ak_table

router = APIRouter(tags=['停复牌', '业绩'])


def _ak(name: str, params: dict, df) -> AkTableOut:
    return wrap_ak_table(name, params, df)


# —— 19-22 ——


@router.get(
    "/stock/em/tfp",
    response_model=Response,
    summary="停复牌信息（东财）",
    description="封装 `ak.stock_tfp_em`；`date` 为 YYYYMMDD。",
)
async def em_tfp(
    date: str = Query("20240426", description="交易日 YYYYMMDD。"),
) -> Response:
    p = {"date": date}
    try:
        df = await run_in_threadpool(lambda: ak.stock_tfp_em(date=date))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_tfp_em", p, df))


@router.get(
    "/stock/em/trade-notify-suspend-baidu",
    response_model=Response,
    summary="停复牌-百度源（ak.news_trade_notify_suspend_baidu）",
    description="封装 `ak.news_trade_notify_suspend_baidu`；可选 `cookie` 为百度 Cookie。",
)
async def em_trade_notify_suspend_baidu(
    date: str = Query("20241107", description="日期 YYYYMMDD，与 `date` 入参一致。"),
    cookie: str | None = Query(None, description="可选。百度站 Cookie 字符串，不传则与 AKShare 默认行为一致。"),
) -> Response:
    p: dict = {"date": date}
    if cookie is not None:
        p["cookie"] = cookie
    try:
        df = await run_in_threadpool(
            lambda: ak.news_trade_notify_suspend_baidu(date=date, cookie=cookie)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("news_trade_notify_suspend_baidu", p, df))


@router.get(
    "/stock/em/yjkb",
    response_model=Response,
    summary="业绩快报（东财）",
    description="封装 `ak.stock_yjkb_em`；`date` 为报告期等，格式见 AKShare 说明。",
)
async def em_yjkb(
    date: str = Query("20200331", description="如季度末 20200331 等。与 AKShare 入参 `date` 一致。"),
) -> Response:
    p = {"date": date}
    try:
        df = await run_in_threadpool(lambda: ak.stock_yjkb_em(date=date))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_yjkb_em", p, df))


@router.get(
    "/stock/em/yjyg",
    response_model=Response,
    summary="业绩预告（东财）",
    description="封装 `ak.stock_yjyg_em`。",
)
async def em_yjyg(
    date: str = Query("20190331", description="报告期 YYYYMMDD。"),
) -> Response:
    p = {"date": date}
    try:
        df = await run_in_threadpool(lambda: ak.stock_yjyg_em(date=date))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_yjyg_em", p, df))
