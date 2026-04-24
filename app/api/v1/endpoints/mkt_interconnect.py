"""北向/南向、沪深港通等互联互通数据。"""
from __future__ import annotations

import akshare as ak
from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.schemas.ak_table import AkTableOut
from app.schemas.response import Response
from app.utils.ak_response import wrap_ak_table

router = APIRouter(tags=['沪深港通'])


def _ak(name: str, params: dict, df) -> AkTableOut:
    return wrap_ak_table(name, params, df)



@router.get(
    "/stock/em/hsgt/fund-flow/summary",
    response_model=Response,
    summary="沪深港通资金流向总览（东财）",
    description="封装 `ak.stock_hsgt_fund_flow_summary_em`，无入参。",
)
async def em_hsgt_fund_flow_summary() -> Response:
    try:
        df = await run_in_threadpool(ak.stock_hsgt_fund_flow_summary_em)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_hsgt_fund_flow_summary_em", {}, df))


@router.get(
    "/stock/em/hsgt/fund-min",
    response_model=Response,
    summary="沪深港通分时数据（东财）",
    description="封装 `ak.stock_hsgt_fund_min_em`；`symbol` 如 北向资金/南向资金 等。",
)
async def em_hsgt_fund_min(
    symbol: str = Query("北向资金", description="如：北向资金、南向资金等，以 AKShare 支持为准。"),
) -> Response:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(lambda: ak.stock_hsgt_fund_min_em(symbol=symbol))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_hsgt_fund_min_em", p, df))


@router.get(
    "/stock/em/hsgt/board-rank",
    response_model=Response,
    summary="沪深港通持股板块排行（东财）",
    description="封装 `ak.stock_hsgt_board_rank_em`；`symbol` 为排行表名称，如 北向资金增持行业板块排行。",
)
async def em_hsgt_board_rank(
    symbol: str = Query("北向资金增持行业板块排行", description="排行表名称/标识。"),
    indicator: str = Query("今日", description="如 今日/3日/5日 等。与 AKShare 文档一致。"),
) -> Response:
    p = {"symbol": symbol, "indicator": indicator}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_hsgt_board_rank_em(symbol=symbol, indicator=indicator)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_hsgt_board_rank_em", p, df))


@router.get(
    "/stock/em/hsgt/hold-stock",
    response_model=Response,
    summary="沪深港通持股个股排行（东财）",
    description="封装 `ak.stock_hsgt_hold_stock_em`；`market`·`indicator` 组合见官方示例。",
)
async def em_hsgt_hold_stock(
    market: str = Query("北向", description="如 北向/南向/沪股通/深股通 等。"),
    indicator: str = Query("今日排行", description="如 5日排名/今日排行 等。"),
) -> Response:
    p = {"market": market, "indicator": indicator}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_hsgt_hold_stock_em(market=market, indicator=indicator)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_hsgt_hold_stock_em", p, df))


@router.get(
    "/stock/em/hsgt/stock-statistics",
    response_model=Response,
    summary="沪深港通持股每日个股统计（东财）",
    description="封装 `ak.stock_hsgt_stock_statistics_em`；`symbol` 为统计表类型名，如 北向持股。",
)
async def em_hsgt_stock_statistics(
    symbol: str = Query("北向持股", description="表类型/名称。例 北向持股。"),
    start_date: str = Query("20211027", description="开始，YYYYMMDD。"),
    end_date: str = Query("20211027", description="结束，YYYYMMDD。"),
) -> Response:
    p = {
        "symbol": symbol,
        "start_date": start_date,
        "end_date": end_date,
    }
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_hsgt_stock_statistics_em(
                symbol=symbol, start_date=start_date, end_date=end_date
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_hsgt_stock_statistics_em", p, df))


@router.get(
    "/stock/em/hsgt/institution-statistics",
    response_model=Response,
    summary="沪深港通持股机构排行（东财）",
    description="封装 `ak.stock_hsgt_institution_statistics_em`；`market` 如 北向持股。",
)
async def em_hsgt_institution_statistics(
    market: str = Query("北向持股", description="市场/类型。例 北向持股。"),
    start_date: str = Query("20201218", description="开始 YYYYMMDD。"),
    end_date: str = Query("20201218", description="结束 YYYYMMDD。"),
) -> Response:
    p = {
        "market": market,
        "start_date": start_date,
        "end_date": end_date,
    }
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_hsgt_institution_statistics_em(
                market=market, start_date=start_date, end_date=end_date
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_hsgt_institution_statistics_em", p, df))


@router.get(
    "/stock/em/hsgt/sh-hk-spot",
    response_model=Response,
    summary="沪深港通实时行情（东财）",
    description="封装 `ak.stock_hsgt_sh_hk_spot_em`，无入参。",
)
async def em_hsgt_sh_hk_spot() -> Response:
    try:
        df = await run_in_threadpool(ak.stock_hsgt_sh_hk_spot_em)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_hsgt_sh_hk_spot_em", {}, df))


@router.get(
    "/stock/em/hsgt/hist",
    response_model=Response,
    summary="沪深港通历史数据（东财）",
    description="封装 `ak.stock_hsgt_hist_em`；`symbol` 如 北向资金。",
)
async def em_hsgt_hist(
    symbol: str = Query("北向资金", description="资金类型名称，以 AKShare 支持为准。"),
) -> Response:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(lambda: ak.stock_hsgt_hist_em(symbol=symbol))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_hsgt_hist_em", p, df))


@router.get(
    "/stock/em/hsgt/individual",
    response_model=Response,
    summary="沪深港通持股个股（东财）",
    description="封装 `ak.stock_hsgt_individual_em`；A 股代码 6 位。",
)
async def em_hsgt_individual(
    symbol: str = Query("002008", description="股票代码，6 位。"),
) -> Response:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_hsgt_individual_em(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_hsgt_individual_em", p, df))


@router.get(
    "/stock/em/hsgt/individual-detail",
    response_model=Response,
    summary="沪深港通持股个股详情（东财）",
    description="封装 `ak.stock_hsgt_individual_detail_em`；`start_date`/`end_date` 为 YYYYMMDD。",
)
async def em_hsgt_individual_detail(
    symbol: str = Query("002008", description="股票代码。"),
    start_date: str = Query("20210830", description="开始，YYYYMMDD。"),
    end_date: str = Query("20211026", description="结束，YYYYMMDD。"),
) -> Response:
    p = {
        "symbol": symbol,
        "start_date": start_date,
        "end_date": end_date,
    }
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_hsgt_individual_detail_em(
                symbol=symbol, start_date=start_date, end_date=end_date
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_hsgt_individual_detail_em", p, df))
