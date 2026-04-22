"""财经快讯、早餐、多源全球资讯。"""

from __future__ import annotations

import akshare as ak
from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.schemas.ak_table import AkTableOut
from app.utils.ak_response import wrap_ak_table

_DOC = "https://akshare.akfamily.xyz/data/stock/stock.html"
router = APIRouter(tags=["资讯", "快讯", "财联社", "东财", "新浪", "富途"])


def _ak(name: str, params: dict, df) -> AkTableOut:
    return wrap_ak_table(name, params, df)


@router.get(
    "/stock/em/info/cjzc",
    response_model=AkTableOut,
    summary="财经早餐（东财）",
    description=f"封装 `ak.stock_info_cjzc_em`，无入参。文档：[A 股数据]({_DOC})。",
)
async def em_info_cjzc() -> AkTableOut:
    try:
        df = await run_in_threadpool(ak.stock_info_cjzc_em)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_info_cjzc_em", {}, df)


@router.get(
    "/stock/em/info/global",
    response_model=AkTableOut,
    summary="全球财经快讯（东财）",
    description="封装 `ak.stock_info_global_em`，无入参。",
)
async def em_info_global() -> AkTableOut:
    try:
        df = await run_in_threadpool(ak.stock_info_global_em)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_info_global_em", {}, df)


@router.get(
    "/stock/sina/info/global",
    response_model=AkTableOut,
    summary="全球财经快讯（新浪）",
    description="封装 `ak.stock_info_global_sina`，无入参。",
)
async def sina_info_global() -> AkTableOut:
    try:
        df = await run_in_threadpool(ak.stock_info_global_sina)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_info_global_sina", {}, df)


@router.get(
    "/stock/futu/info/global",
    response_model=AkTableOut,
    summary="快讯（富途牛牛）",
    description="封装 `ak.stock_info_global_futu`，无入参。",
)
async def futu_info_global() -> AkTableOut:
    try:
        df = await run_in_threadpool(ak.stock_info_global_futu)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_info_global_futu", {}, df)


@router.get(
    "/stock/cls/telegraph",
    response_model=AkTableOut,
    summary="电报（财联社）",
    description="封装 `ak.stock_info_global_cls`；`symbol` 如 全部/重点 等与官方一致。",
)
async def cls_telegraph(
    symbol: str = Query("全部", description="频道或范围。例 全部。"),
) -> AkTableOut:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_info_global_cls(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_info_global_cls", p, df)
