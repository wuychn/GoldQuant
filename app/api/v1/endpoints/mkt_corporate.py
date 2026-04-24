"""公司互动、动态与新股相关。"""
from __future__ import annotations

import akshare as ak
from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.schemas.ak_table import AkTableOut
from app.schemas.response import Response
from app.utils.ak_response import wrap_ak_table

router = APIRouter(tags=["公司", "新股", "互动", "盘前", "评论"])

_DOC = "https://akshare.akfamily.xyz/data/stock/stock.html"


def _ak(name: str, params: dict, df) -> AkTableOut:
    return wrap_ak_table(name, params, df)


@router.get(
    "/stock/em/zh-a-hist-pre-min",
    response_model=Response,
    summary="盘前数据（东财）",
    description=f"封装 `ak.stock_zh_a_hist_pre_min_em`。[盘前数据]({_DOC}#盘前数据)。",
)
async def em_zh_a_hist_pre_min(
    symbol: str = Query("000001", description="股票代码。"),
    start_time: str = Query("09:00:00", description="开始时间，如 09:00:00。"),
    end_time: str = Query("15:40:00", description="结束时间，如 15:40:00。AKShare 默认 15:50:00 亦可自填。"),
) -> Response:
    p = {"symbol": symbol, "start_time": start_time, "end_time": end_time}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_zh_a_hist_pre_min_em(
                symbol=symbol, start_time=start_time, end_time=end_time
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_zh_a_hist_pre_min_em", p, df))


@router.get(
    "/stock/em/gsrl-gsdt",
    response_model=Response,
    summary="公司动态（东财）",
    description=f"封装 `ak.stock_gsrl_gsdt_em`。[公司动态]({_DOC})。",
)
async def em_gsrl_gsdt(
    date: str = Query("20230808", description="日期，YYYYMMDD，与 AKShare 入参 `date` 一致。"),
) -> Response:
    p = {"date": date}
    try:
        df = await run_in_threadpool(lambda: ak.stock_gsrl_gsdt_em(date=date))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_gsrl_gsdt_em", p, df))


@router.get(
    "/stock/em/zh-a-new",
    response_model=Response,
    summary="新股（东财）",
    description="封装 `ak.stock_zh_a_new_em`，无入参。",
)
async def em_zh_a_new() -> Response:
    p: dict = {}
    try:
        df = await run_in_threadpool(ak.stock_zh_a_new_em)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_zh_a_new_em", p, df))


@router.get(
    "/stock/em/zygc",
    response_model=Response,
    summary="主营构成（东财）",
    description="封装 `ak.stock_zygc_em`；`symbol` 为带市场前缀代码，如 SH688041。",
)
async def em_zygc(
    symbol: str = Query("SH688041", description="股票代码，带市场前缀。例 SH688041。"),
) -> Response:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(lambda: ak.stock_zygc_em(symbol=symbol))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_zygc_em", p, df))


@router.get(
    "/stock/em/comment-scrd-focus",
    response_model=Response,
    summary="用户关注指数（东财）",
    description="封装 `ak.stock_comment_detail_scrd_focus_em`。",
)
async def em_comment_scrd_focus(
    symbol: str = Query("600000", description="A 股代码，如 600000。"),
) -> Response:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_comment_detail_scrd_focus_em(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_comment_detail_scrd_focus_em", p, df))


@router.get(
    "/stock/em/comment-scrd-desire",
    response_model=Response,
    summary="市场参与意愿（东财）",
    description="封装 `ak.stock_comment_detail_scrd_desire_em`。",
)
async def em_comment_scrd_desire(
    symbol: str = Query("600000", description="A 股代码。例 600000。"),
) -> Response:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_comment_detail_scrd_desire_em(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_comment_detail_scrd_desire_em", p, df))


# —— 同花顺 新股/主营（原 stock_ths_extended 前缀 /stock/ths） ——


@router.get(
    "/stock/ths/xgsr",
    response_model=Response,
    summary="新股上市首日（同花顺）",
    description="封装 `ak.stock_xgsr_ths`，无入参。",
)
async def ths_xgsr() -> Response:
    try:
        df = await run_in_threadpool(ak.stock_xgsr_ths)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_xgsr_ths", {}, df))


@router.get(
    "/stock/ths/zyjs",
    response_model=Response,
    summary="主营介绍（同花顺）",
    description="封装 `ak.stock_zyjs_ths`；`symbol` 为 6 位代码。",
)
async def ths_zyjs(
    symbol: str = Query("000066", description="股票代码 6 位。例 000066。"),
) -> Response:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_zyjs_ths(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_zyjs_ths", p, df))

