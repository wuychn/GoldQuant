"""实时行情、K 线、分时，含东财、新浪与筹码分布。"""

from __future__ import annotations

from typing import Literal

import akshare as ak
from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.schemas.ak_openapi import (
    EmBidAskIn,
    EmBidAskOut,
    EmIndividualInfoIn,
    EmIndividualInfoOut,
    EmIntradayIn,
    EmIntradayOut,
    EmZhAHistIn,
    EmZhAHistOut,
    EmZhAHistMinIn,
    EmZhAHistMinOut,
    SinaIntradayIn,
    SinaIntradayOut,
    SinaMinuteIn,
    SinaMinuteOut,
    SinaZhADailyIn,
    SinaZhADailyOut,
    field_desc,
)
from app.schemas.ak_table import AkTableOut
from app.schemas.response import Response
from app.utils.ak_response import wrap_ak_dataframe, wrap_ak_table

router = APIRouter(tags=["行情", "K线", "分时", "新浪行情", "筹码分布"])

_BASE = "https://akshare.akfamily.xyz/data/stock/stock.html"


def _ak(name: str, params: dict, df) -> AkTableOut:
    return wrap_ak_table(name, params, df)


@router.get(
    "/stock/em/individual-info",
    response_model=Response,
    summary="个股信息查询（东财）",
    description=(
        "封装 `ak.stock_individual_info_em`。"
        f"文档：[个股信息查询-东财]({_BASE}#个股信息查询-东财)。"
    ),
)
async def stock_individual_info_em(
    symbol: str = Query(
        ...,
        description=field_desc(EmIndividualInfoIn, "symbol"),
        examples=["000001"],
    ),
    timeout: float | None = Query(
        None,
        gt=0,
        description=field_desc(EmIndividualInfoIn, "timeout"),
    ),
) -> Response:
    def _call():
        if timeout is not None:
            return ak.stock_individual_info_em(symbol=symbol, timeout=timeout)
        return ak.stock_individual_info_em(symbol=symbol)

    try:
        df = await run_in_threadpool(_call)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(
        data=wrap_ak_dataframe(
            EmIndividualInfoOut,
            "stock_individual_info_em",
            EmIndividualInfoIn(symbol=symbol, timeout=timeout),
            df,
        )
    )


@router.get(
    "/stock/em/bid-ask",
    response_model=Response,
    summary="行情报价（东财）",
    description=(f"封装 `ak.stock_bid_ask_em`。[行情报价]({_BASE}#行情报价)。"),
)
async def stock_bid_ask_em(
    symbol: str = Query(
        ...,
        description=field_desc(EmBidAskIn, "symbol"),
        examples=["000001"],
    ),
) -> Response:
    try:
        df = await run_in_threadpool(ak.stock_bid_ask_em, symbol)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(
        data=wrap_ak_dataframe(
            EmBidAskOut, "stock_bid_ask_em", EmBidAskIn(symbol=symbol), df
        )
    )


@router.get(
    "/stock/em/zh-a-hist",
    response_model=Response,
    summary="历史行情数据（东财，日/周/月）",
    description=(f"封装 `ak.stock_zh_a_hist`。[历史行情数据-东财]({_BASE}#历史行情数据-东财)。"),
)
async def stock_zh_a_hist(
    symbol: str = Query(
        ...,
        description=field_desc(EmZhAHistIn, "symbol"),
        examples=["000001"],
    ),
    period: Literal["daily", "weekly", "monthly"] = Query(
        "daily",
        description=field_desc(EmZhAHistIn, "period"),
    ),
    start_date: str = Query(
        ...,
        description=field_desc(EmZhAHistIn, "start_date"),
        examples=["20170301"],
        pattern=r"^\d{8}$",
    ),
    end_date: str = Query(
        ...,
        description=field_desc(EmZhAHistIn, "end_date"),
        examples=["20240528"],
        pattern=r"^\d{8}$",
    ),
    adjust: str = Query(
        "",
        description=field_desc(EmZhAHistIn, "adjust"),
        examples=["", "qfq", "hfq"],
    ),
    timeout: float | None = Query(
        None,
        gt=0,
        description=field_desc(EmZhAHistIn, "timeout"),
    ),
) -> Response:
    def _call():
        kw = {
            "symbol": symbol,
            "period": period,
            "start_date": start_date,
            "end_date": end_date,
            "adjust": adjust,
        }
        if timeout is not None:
            kw["timeout"] = timeout
        return ak.stock_zh_a_hist(**kw)

    try:
        df = await run_in_threadpool(_call)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(
        data=wrap_ak_dataframe(
            EmZhAHistOut,
            "stock_zh_a_hist",
            EmZhAHistIn(
                symbol=symbol,
                period=period,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
                timeout=timeout,
            ),
            df,
        )
    )


@router.get(
    "/stock/em/zh-a-hist-min",
    response_model=Response,
    summary="分时数据（东财，历史分钟）",
    description=(f"封装 `ak.stock_zh_a_hist_min_em`。[分时数据-东财]({_BASE}#分时数据-东财)。"),
)
async def stock_zh_a_hist_min_em(
    symbol: str = Query(
        ...,
        description=field_desc(EmZhAHistMinIn, "symbol"),
        examples=["000001"],
    ),
    start_date: str = Query(
        ...,
        description=field_desc(EmZhAHistMinIn, "start_date"),
        examples=["2024-03-20 09:30:00"],
    ),
    end_date: str = Query(
        ...,
        description=field_desc(EmZhAHistMinIn, "end_date"),
        examples=["2024-03-20 15:00:00"],
    ),
    period: Literal["1", "5", "15", "30", "60"] = Query(
        "5",
        description=field_desc(EmZhAHistMinIn, "period"),
    ),
    adjust: str = Query(
        "",
        description=field_desc(EmZhAHistMinIn, "adjust"),
        examples=["", "qfq", "hfq"],
    ),
) -> Response:
    try:
        df = await run_in_threadpool(
            ak.stock_zh_a_hist_min_em,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            period=period,
            adjust=adjust,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(
        data=wrap_ak_dataframe(
            EmZhAHistMinOut,
            "stock_zh_a_hist_min_em",
            EmZhAHistMinIn(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                period=period,
                adjust=adjust,
            ),
            df,
        )
    )


@router.get(
    "/stock/em/intraday",
    response_model=Response,
    summary="日内分时数据（东财）",
    description=(f"封装 `ak.stock_intraday_em`。[日内分时数据-东财]({_BASE}#日内分时数据-东财)。"),
)
async def stock_intraday_em(
    symbol: str = Query(
        ...,
        description=field_desc(EmIntradayIn, "symbol"),
        examples=["000001"],
    ),
) -> Response:
    try:
        df = await run_in_threadpool(ak.stock_intraday_em, symbol=symbol)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(
        data=wrap_ak_dataframe(
            EmIntradayOut, "stock_intraday_em", EmIntradayIn(symbol=symbol), df
        )
    )


@router.get(
    "/stock/sina/zh-a-daily",
    response_model=Response,
    summary="历史行情数据（新浪，日线）",
    description=(f"封装 `ak.stock_zh_a_daily`。[历史行情数据-新浪]({_BASE}#历史行情数据-新浪)。"),
)
async def stock_zh_a_daily(
    symbol: str = Query(
        ...,
        description=field_desc(SinaZhADailyIn, "symbol"),
        examples=["sz000001"],
    ),
    start_date: str = Query(
        ...,
        description=field_desc(SinaZhADailyIn, "start_date"),
        examples=["19910403"],
        pattern=r"^\d{8}$",
    ),
    end_date: str = Query(
        ...,
        description=field_desc(SinaZhADailyIn, "end_date"),
        examples=["20231027"],
        pattern=r"^\d{8}$",
    ),
    adjust: str = Query(
        "",
        description=field_desc(SinaZhADailyIn, "adjust"),
        examples=["", "qfq", "hfq"],
    ),
) -> Response:
    try:
        df = await run_in_threadpool(
            ak.stock_zh_a_daily,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(
        data=wrap_ak_dataframe(
            SinaZhADailyOut,
            "stock_zh_a_daily",
            SinaZhADailyIn(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            ),
            df,
        )
    )


@router.get(
    "/stock/sina/minute",
    response_model=Response,
    summary="分时数据（新浪，分钟 K）",
    description=(f"封装 `ak.stock_zh_a_minute`。[分时数据-新浪]({_BASE}#分时数据-新浪)。"),
)
async def stock_zh_a_minute(
    symbol: str = Query(
        ...,
        description=field_desc(SinaMinuteIn, "symbol"),
        examples=["sh600751"],
    ),
    period: Literal["1", "5", "15", "30", "60"] = Query(
        "1",
        description=field_desc(SinaMinuteIn, "period"),
    ),
    adjust: str = Query(
        "",
        description=field_desc(SinaMinuteIn, "adjust"),
        examples=["qfq", "", "hfq"],
    ),
) -> Response:
    try:
        df = await run_in_threadpool(
            ak.stock_zh_a_minute,
            symbol=symbol,
            period=period,
            adjust=adjust,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(
        data=wrap_ak_dataframe(
            SinaMinuteOut,
            "stock_zh_a_minute",
            SinaMinuteIn(symbol=symbol, period=period, adjust=adjust),
            df,
        )
    )


@router.get(
    "/stock/sina/intraday",
    response_model=Response,
    summary="日内分时数据（新浪，大单）",
    description=(f"封装 `ak.stock_intraday_sina`。[日内分时数据-新浪]({_BASE}#日内分时数据-新浪)。"),
)
async def stock_intraday_sina(
    symbol: str = Query(
        ...,
        description=field_desc(SinaIntradayIn, "symbol"),
        examples=["sz000001"],
    ),
    date: str = Query(
        ...,
        description=field_desc(SinaIntradayIn, "date"),
        examples=["20240321"],
        pattern=r"^\d{8}$",
    ),
) -> Response:
    try:
        df = await run_in_threadpool(
            ak.stock_intraday_sina,
            symbol=symbol,
            date=date,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(
        data=wrap_ak_dataframe(
            SinaIntradayOut,
            "stock_intraday_sina",
            SinaIntradayIn(symbol=symbol, date=date),
            df,
        )
    )


@router.get(
    "/stock/em/cyq",
    response_model=Response,
    summary="筹码分布（东方财富）",
    description="封装 `ak.stock_cyq_em`；`adjust` 同东财日 K 复权入参。",
)
async def em_cyq(
    symbol: str = Query("000001", description="6 位代码。"),
    adjust: str = Query("", description="空=不复权；`qfq` 前；`hfq` 后。"),
) -> Response:
    p = {"symbol": symbol, "adjust": adjust}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_cyq_em(symbol=symbol, adjust=adjust)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=_ak("stock_cyq_em", p, df))
