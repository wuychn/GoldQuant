"""概念、行业与板块：列表、成份、K 线/分时及板块指数/简介（AKShare 封装，具体来源见各接口说明）。"""

from __future__ import annotations

import akshare as ak
from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.schemas.ak_openapi import ThsIndustrySummaryOut
from app.schemas.ak_table import AkTableOut
from app.utils.ak_response import wrap_ak_dataframe, wrap_ak_table

_DOC = "https://akshare.akfamily.xyz/data/stock/stock.html"
router = APIRouter(tags=["板块", "概念", "行业"])


def _ak(name: str, params: dict, df) -> AkTableOut:
    return wrap_ak_table(name, params, df)


@router.get(
    "/stock/em/board-concept/names",
    response_model=AkTableOut,
    summary="概念板块名称列表（东财）",
    description="封装 `ak.stock_board_concept_name_em`，无入参。",
)
async def em_board_concept_name() -> AkTableOut:
    try:
        df = await run_in_threadpool(ak.stock_board_concept_name_em)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_board_concept_name_em", {}, df)


@router.get(
    "/stock/em/board-concept/spot",
    response_model=AkTableOut,
    summary="概念板块-实时行情（东财）",
    description="封装 `ak.stock_board_concept_spot_em`；`symbol` 为概念名。",
)
async def em_board_concept_spot(
    symbol: str = Query("可燃冰", description="概念名称。例 可燃冰。"),
) -> AkTableOut:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_board_concept_spot_em(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_board_concept_spot_em", p, df)


@router.get(
    "/stock/em/board-concept/cons",
    response_model=AkTableOut,
    summary="概念板块成份股（东财）",
    description="封装 `ak.stock_board_concept_cons_em`；`symbol` 为概念名。",
)
async def em_board_concept_cons(
    symbol: str = Query("融资融券", description="概念名称。例 融资融券。"),
) -> AkTableOut:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_board_concept_cons_em(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_board_concept_cons_em", p, df)


@router.get(
    "/stock/em/board-concept/hist",
    response_model=AkTableOut,
    summary="概念板块-历史K线（东财）",
    description="封装 `ak.stock_board_concept_hist_em`；`period` 同东财，复权同 `stock_zh_a_hist` 约定。",
)
async def em_board_concept_hist(
    symbol: str = Query("绿色电力", description="概念名称。例 绿色电力。"),
    period: str = Query("daily", description="K 线周期。例 daily, weekly, monthly 等。与官方一致。"),
    start_date: str = Query("20220101", description="开始 YYYYMMDD。"),
    end_date: str = Query("20250227", description="结束 YYYYMMDD。"),
    adjust: str = Query("", description="复权：空/qfq/hfq。与 `stock_zh_a_hist` 一致。"),
) -> AkTableOut:
    p = {
        "symbol": symbol,
        "period": period,
        "start_date": start_date,
        "end_date": end_date,
        "adjust": adjust,
    }
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_board_concept_hist_em(
                symbol=symbol,
                period=period,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_board_concept_hist_em", p, df)


@router.get(
    "/stock/em/board-concept/hist-min",
    response_model=AkTableOut,
    summary="概念板块-历史分时/分钟K（东财）",
    description="封装 `ak.stock_board_concept_hist_min_em`；`period` 为分钟，如 1, 5, 15。",
)
async def em_board_concept_hist_min(
    symbol: str = Query("长寿药", description="概念名。例 长寿药。"),
    period: str = Query("5", description="分钟周期 1/5/15/30/60 等。与官方一致。"),
) -> AkTableOut:
    p = {"symbol": symbol, "period": period}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_board_concept_hist_min_em(
                symbol=symbol, period=period
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_board_concept_hist_min_em", p, df)


# —— 概念：板块指数/简介（AKShare 封装） ——


@router.get(
    "/stock/ths/board-concept/index",
    response_model=AkTableOut,
    summary="同花顺-概念板块指数",
    description="封装 `ak.stock_board_concept_index_ths`；K 线区间。",
)
async def ths_board_concept_index(
    symbol: str = Query("阿里巴巴概念", description="概念名称。例 阿里巴巴概念。"),
    start_date: str = Query("20200101", description="开始 YYYYMMDD。"),
    end_date: str = Query("20250321", description="结束 YYYYMMDD。"),
) -> AkTableOut:
    p = {
        "symbol": symbol,
        "start_date": start_date,
        "end_date": end_date,
    }
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_board_concept_index_ths(
                symbol=symbol, start_date=start_date, end_date=end_date
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_board_concept_index_ths", p, df)


@router.get(
    "/stock/ths/board-concept/info",
    response_model=AkTableOut,
    summary="同花顺-概念板块简介",
    description="封装 `ak.stock_board_concept_info_ths`；`symbol` 为概念名。",
)
async def ths_board_concept_info(
    symbol: str = Query("阿里巴巴概念", description="概念名称。例 阿里巴巴概念。"),
) -> AkTableOut:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_board_concept_info_ths(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_board_concept_info_ths", p, df)


# —— 行业：一览与指数（AKShare 封装） ——


@router.get(
    "/board/ths/industry-summary",
    response_model=ThsIndustrySummaryOut,
    summary="行业列表（概览，AKShare 封装）",
    description="封装 `ak.stock_board_industry_summary_ths`。",
)
async def ths_industry_board_summary() -> ThsIndustrySummaryOut:
    try:
        df = await run_in_threadpool(ak.stock_board_industry_summary_ths)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return wrap_ak_dataframe(
        ThsIndustrySummaryOut, "stock_board_industry_summary_ths", {}, df
    )


@router.get(
    "/board/ths/industry-index",
    response_model=AkTableOut,
    summary="行业指数（AKShare 封装）",
    description=(
        "封装 `ak.stock_board_industry_index_ths`；`symbol` 为行业名。"
        f"文档：[A 股数据]({_DOC})。"
    ),
)
async def ths_board_industry_index(
    symbol: str = Query("元件", description="行业名称。例 元件。"),
    start_date: str = Query("20240101", description="开始 YYYYMMDD。"),
    end_date: str = Query("20240718", description="结束 YYYYMMDD。"),
) -> AkTableOut:
    p = {"symbol": symbol, "start_date": start_date, "end_date": end_date}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_board_industry_index_ths(
                symbol=symbol, start_date=start_date, end_date=end_date
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_board_industry_index_ths", p, df)


# —— 行业：东财 列表/行情/成份/K ——


@router.get(
    "/stock/em/board-industry/names",
    response_model=AkTableOut,
    summary="行业板块名称列表（东财）",
    description=f"封装 `ak.stock_board_industry_name_em`，无入参。文档：[A 股数据]({_DOC})。",
)
async def em_board_industry_name() -> AkTableOut:
    try:
        df = await run_in_threadpool(ak.stock_board_industry_name_em)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_board_industry_name_em", {}, df)


@router.get(
    "/stock/em/board-industry/spot",
    response_model=AkTableOut,
    summary="行业板块-实时行情（东财）",
    description="封装 `ak.stock_board_industry_spot_em`；`symbol` 为行业名。",
)
async def em_board_industry_spot(
    symbol: str = Query("小金属", description="行业名称。例 小金属。"),
) -> AkTableOut:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_board_industry_spot_em(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_board_industry_spot_em", p, df)


@router.get(
    "/stock/em/board-industry/cons",
    response_model=AkTableOut,
    summary="行业板块成份股（东财）",
    description="封装 `ak.stock_board_industry_cons_em`；`symbol` 为行业名。",
)
async def em_board_industry_cons(
    symbol: str = Query("小金属", description="行业名称。例 小金属。"),
) -> AkTableOut:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_board_industry_cons_em(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_board_industry_cons_em", p, df)


@router.get(
    "/stock/em/board-industry/hist",
    response_model=AkTableOut,
    summary="行业板块-历史K线日频（东财）",
    description=(
        "封装 `ak.stock_board_industry_hist_em`；`period` 如 日k/周k/月k 与官方一致；"
        "`adjust` 复权约定同 `stock_zh_a_hist`。"
    ),
)
async def em_board_industry_hist(
    symbol: str = Query("小金属", description="行业名称。例 小金属。"),
    start_date: str = Query("20211201", description="开始 YYYYMMDD。"),
    end_date: str = Query("20240222", description="结束 YYYYMMDD。"),
    period: str = Query("日k", description="K 线周期，如 日k、周k、月k。与 AKShare 文档一致。"),
    adjust: str = Query("", description="复权：空（不复权）/ qfq / hfq。"),
) -> AkTableOut:
    p = {
        "symbol": symbol,
        "start_date": start_date,
        "end_date": end_date,
        "period": period,
        "adjust": adjust,
    }
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_board_industry_hist_em(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                period=period,
                adjust=adjust,
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_board_industry_hist_em", p, df)


@router.get(
    "/stock/em/board-industry/hist-min",
    response_model=AkTableOut,
    summary="行业板块-历史分时/分钟K（东财）",
    description="封装 `ak.stock_board_industry_hist_min_em`；`period` 为分钟，如 1, 5, 15。",
)
async def em_board_industry_hist_min(
    symbol: str = Query("小金属", description="行业名称。例 小金属。"),
    period: str = Query("1", description="分钟周期 1/5/15/30/60 等。与官方一致。"),
) -> AkTableOut:
    p = {"symbol": symbol, "period": period}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_board_industry_hist_min_em(
                symbol=symbol, period=period
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_board_industry_hist_min_em", p, df)
