"""个股与板块资金流向、主力与大盘。"""

from __future__ import annotations

import akshare as ak
from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.schemas.ak_table import AkTableOut
from app.utils.ak_response import wrap_ak_table

router = APIRouter(tags=["资金面"])


def _ak(name: str, params: dict, df) -> AkTableOut:
    return wrap_ak_table(name, params, df)


@router.get(
    "/stock/em/fund/individual",
    response_model=AkTableOut,
    summary="个股资金流（东方财富）",
    description="封装 `ak.stock_individual_fund_flow`；`stock` 为代码，`market` 如 sh/sz。",
)
async def em_fund_individual(
    stock: str = Query("600094", description="股票代码，6 位。"),
    market: str = Query("sh", description="市场：如 sh、sz 等。与 AKShare 一致。"),
) -> AkTableOut:
    p = {"stock": stock, "market": market}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_individual_fund_flow(stock=stock, market=market)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_individual_fund_flow", p, df)


@router.get(
    "/stock/em/fund/individual-rank",
    response_model=AkTableOut,
    summary="个股资金流排名（东方财富）",
    description="封装 `ak.stock_individual_fund_flow_rank`；`indicator` 如 今日。",
)
async def em_fund_individual_rank(
    indicator: str = Query("今日", description="周期/指标名，以 AKShare 支持为准。例 今日、5日 等。"),
) -> AkTableOut:
    p = {"indicator": indicator}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_individual_fund_flow_rank(indicator=indicator)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_individual_fund_flow_rank", p, df)


@router.get(
    "/stock/em/fund/market",
    response_model=AkTableOut,
    summary="大盘资金流（东方财富）",
    description="封装 `ak.stock_market_fund_flow`，无入参。",
)
async def em_fund_market() -> AkTableOut:
    try:
        df = await run_in_threadpool(ak.stock_market_fund_flow)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_market_fund_flow", {}, df)


@router.get(
    "/stock/em/fund/sector-rank",
    response_model=AkTableOut,
    summary="板块资金流排名（东方财富）",
    description="封装 `ak.stock_sector_fund_flow_rank`；`sector_type` 如 行业资金流/概念资金流 等。",
)
async def em_fund_sector_rank(
    indicator: str = Query("今日", description="如 今日、5日 等。"),
    sector_type: str = Query("行业资金流", description="如 行业资金流/概念资金流。"),
) -> AkTableOut:
    p = {"indicator": indicator, "sector_type": sector_type}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_sector_fund_flow_rank(
                indicator=indicator, sector_type=sector_type
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_sector_fund_flow_rank", p, df)


@router.get(
    "/stock/em/fund/main",
    response_model=AkTableOut,
    summary="主力净流入排名（东方财富）",
    description="封装 `ak.stock_main_fund_flow`；`symbol` 为品种分类名，如 全部股票。",
)
async def em_fund_main(
    symbol: str = Query("全部股票", description="如 全部股票/沪深A股/创业板 等。"),
) -> AkTableOut:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(lambda: ak.stock_main_fund_flow(symbol=symbol))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_main_fund_flow", p, df)


@router.get(
    "/stock/em/fund/sector-summary",
    response_model=AkTableOut,
    summary="行业个股资金流（东方财富）",
    description="封装 `ak.stock_sector_fund_flow_summary`；`symbol` 为行业名，`indicator` 如 今日。",
)
async def em_fund_sector_summary(
    symbol: str = Query("电源设备", description="行业/板块名。"),
    indicator: str = Query("今日", description="如 今日、3日 等。"),
) -> AkTableOut:
    p = {"symbol": symbol, "indicator": indicator}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_sector_fund_flow_summary(
                symbol=symbol, indicator=indicator
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_sector_fund_flow_summary", p, df)


@router.get(
    "/stock/em/fund/sector-hist",
    response_model=AkTableOut,
    summary="行业历史资金流（东方财富）",
    description="封装 `ak.stock_sector_fund_flow_hist`；`symbol` 为行业名。",
)
async def em_fund_sector_hist(
    symbol: str = Query("汽车服务", description="行业名称。例 汽车服务。"),
) -> AkTableOut:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_sector_fund_flow_hist(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_sector_fund_flow_hist", p, df)


@router.get(
    "/stock/em/fund/concept-hist",
    response_model=AkTableOut,
    summary="概念历史资金流（东方财富）",
    description="封装 `ak.stock_concept_fund_flow_hist`；`symbol` 为概念名。",
)
async def em_fund_concept_hist(
    symbol: str = Query("数据要素", description="概念名。例 数据要素。"),
) -> AkTableOut:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_concept_fund_flow_hist(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_concept_fund_flow_hist", p, df)


# —— 同花顺 资金流（原 prefix /stock/ths） ——


@router.get(
    "/stock/ths/fund-flow/individual",
    response_model=AkTableOut,
    summary="个股资金流向（同花顺）",
    description="封装 `ak.stock_fund_flow_individual`；`symbol` 如 即时/3日/5日/10日/20日 等。",
)
async def ths_fund_flow_individual(
    symbol: str = Query("即时", description="时间窗口/指标名。例 即时、3日 等。与官方一致。"),
) -> AkTableOut:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_fund_flow_individual(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_fund_flow_individual", p, df)


@router.get(
    "/stock/ths/fund-flow/concept",
    response_model=AkTableOut,
    summary="概念资金流向（同花顺）",
    description="封装 `ak.stock_fund_flow_concept`；`symbol` 为时间/指标。",
)
async def ths_fund_flow_concept(
    symbol: str = Query("即时", description="如 即时 等。与官方一致。"),
) -> AkTableOut:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_fund_flow_concept(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_fund_flow_concept", p, df)


@router.get(
    "/stock/ths/fund-flow/industry",
    response_model=AkTableOut,
    summary="行业资金流向（同花顺）",
    description="封装 `ak.stock_fund_flow_industry`；`symbol` 为时间/指标。",
)
async def ths_fund_flow_industry(
    symbol: str = Query("即时", description="如 即时 等。与官方一致。"),
) -> AkTableOut:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_fund_flow_industry(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_fund_flow_industry", p, df)


@router.get(
    "/stock/ths/fund-flow/big-deal",
    response_model=AkTableOut,
    summary="大单追踪（同花顺）",
    description="封装 `ak.stock_fund_flow_big_deal`，无入参。",
)
async def ths_fund_flow_big_deal() -> AkTableOut:
    try:
        df = await run_in_threadpool(ak.stock_fund_flow_big_deal)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_fund_flow_big_deal", {}, df)
