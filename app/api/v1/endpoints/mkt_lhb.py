"""龙虎榜（东财 + 同花顺营业部排行 + 新浪）。"""

from __future__ import annotations

import akshare as ak
from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.schemas.ak_table import AkTableOut
from app.utils.ak_response import wrap_ak_table

router = APIRouter(tags=["龙虎榜"])


def _ak(name: str, params: dict, df) -> AkTableOut:
    return wrap_ak_table(name, params, df)


# —— 东财 龙虎 ——


@router.get(
    "/stock/em/lhb/detail",
    response_model=AkTableOut,
    summary="龙虎榜详情（东财）",
    description="封装 `ak.stock_lhb_detail_em`；日期区间 YYYYMMDD。",
)
async def em_lhb_detail(
    start_date: str = Query("20230403", description="开始，YYYYMMDD。"),
    end_date: str = Query("20230417", description="结束，YYYYMMDD。"),
) -> AkTableOut:
    p = {"start_date": start_date, "end_date": end_date}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_lhb_detail_em(
                start_date=start_date, end_date=end_date
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_lhb_detail_em", p, df)


@router.get(
    "/stock/em/lhb/stock-statistic",
    response_model=AkTableOut,
    summary="龙虎榜个股上榜统计（东财）",
    description="封装 `ak.stock_lhb_stock_statistic_em`；`symbol` 为统计时间窗口描述，如 近一月。",
)
async def em_lhb_stock_statistic(
    symbol: str = Query("近一月", description="如 近一月/近一周 等。与官方可选值一致。"),
) -> AkTableOut:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_lhb_stock_statistic_em(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_lhb_stock_statistic_em", p, df)


@router.get(
    "/stock/em/lhb/jgmmtj",
    response_model=AkTableOut,
    summary="龙虎榜机构买卖每日统计（东财）",
    description="封装 `ak.stock_lhb_jgmmtj_em`；日期为区间。",
)
async def em_lhb_jgmmtj(
    start_date: str = Query("20240417", description="开始 YYYYMMDD。"),
    end_date: str = Query("20240430", description="结束 YYYYMMDD。"),
) -> AkTableOut:
    p = {"start_date": start_date, "end_date": end_date}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_lhb_jgmmtj_em(
                start_date=start_date, end_date=end_date
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_lhb_jgmmtj_em", p, df)


@router.get(
    "/stock/em/lhb/jgstatistic",
    response_model=AkTableOut,
    summary="机构席位追踪（东财·龙虎）",
    description="封装 `ak.stock_lhb_jgstatistic_em`；`symbol` 为统计时间窗口。",
)
async def em_lhb_jgstatistic(
    symbol: str = Query("近一月", description="如 近一月、近一年 等。"),
) -> AkTableOut:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_lhb_jgstatistic_em(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_lhb_jgstatistic_em", p, df)


@router.get(
    "/stock/em/lhb/hyyyb",
    response_model=AkTableOut,
    summary="每日活跃营业部（东财·龙虎）",
    description="封装 `ak.stock_lhb_hyyyb_em`。",
)
async def em_lhb_hyyyb(
    start_date: str = Query("20220324", description="开始 YYYYMMDD。"),
    end_date: str = Query("20220324", description="结束 YYYYMMDD。"),
) -> AkTableOut:
    p = {"start_date": start_date, "end_date": end_date}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_lhb_hyyyb_em(
                start_date=start_date, end_date=end_date
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_lhb_hyyyb_em", p, df)


@router.get(
    "/stock/em/lhb/yyb-detail",
    response_model=AkTableOut,
    summary="营业部详情数据（东财·龙虎）",
    description="封装 `ak.stock_lhb_yyb_detail_em`；`symbol` 为营业部 id。",
)
async def em_lhb_yyb_detail(
    symbol: str = Query("10188715", description="东财返回的营业部 `symbol` 或代码。"),
) -> AkTableOut:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_lhb_yyb_detail_em(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_lhb_yyb_detail_em", p, df)


@router.get(
    "/stock/em/lhb/yybph",
    response_model=AkTableOut,
    summary="营业部排行（东财·龙虎）",
    description="封装 `ak.stock_lhb_yybph_em`；`symbol` 为统计期描述。",
)
async def em_lhb_yybph(
    symbol: str = Query("近一月", description="如 近一月。"),
) -> AkTableOut:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(lambda: ak.stock_lhb_yybph_em(symbol=symbol))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_lhb_yybph_em", p, df)


@router.get(
    "/stock/em/lhb/trader-statistic",
    response_model=AkTableOut,
    summary="营业部统计（东财·龙虎）",
    description="封装 `ak.stock_lhb_traderstatistic_em`；`symbol` 为统计期描述。",
)
async def em_lhb_trader_statistic(
    symbol: str = Query("近一月", description="如 近一月。"),
) -> AkTableOut:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_lhb_traderstatistic_em(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_lhb_traderstatistic_em", p, df)


@router.get(
    "/stock/em/lhb/stock-detail",
    response_model=AkTableOut,
    summary="个股龙虎榜详情（东财·龙虎）",
    description="封装 `ak.stock_lhb_stock_detail_em`；`date` 为 YYYYMMDD，`flag` 如 买入/卖出。",
)
async def em_lhb_stock_detail(
    symbol: str = Query("600077", description="6 位代码或含交易所前缀，与 AKShare 要求一致。"),
    date: str = Query("20070416", description="交易日 YYYYMMDD。"),
    flag: str = Query("买入", description="买卖方向/榜单类型。例 买入、卖出。"),
) -> AkTableOut:
    p = {"symbol": symbol, "date": date, "flag": flag}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_lhb_stock_detail_em(
                symbol=symbol, date=date, flag=flag
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_lhb_stock_detail_em", p, df)


# —— 同花顺 营业部排行 ——


@router.get(
    "/stock/ths/lh/yyb-most",
    response_model=AkTableOut,
    summary="龙虎榜-营业部排行-上榜次数最多（同花顺）",
    description="封装 `ak.stock_lh_yyb_most`，无入参。",
)
async def ths_lh_yyb_most() -> AkTableOut:
    try:
        df = await run_in_threadpool(ak.stock_lh_yyb_most)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_lh_yyb_most", {}, df)


@router.get(
    "/stock/ths/lh/yyb-capital",
    response_model=AkTableOut,
    summary="龙虎榜-营业部排行-资金实力最强（同花顺）",
    description="封装 `ak.stock_lh_yyb_capital`，无入参。",
)
async def ths_lh_yyb_capital() -> AkTableOut:
    try:
        df = await run_in_threadpool(ak.stock_lh_yyb_capital)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_lh_yyb_capital", {}, df)


@router.get(
    "/stock/ths/lh/yyb-control",
    response_model=AkTableOut,
    summary="龙虎榜-营业部排行-抱团操作实力（同花顺）",
    description="封装 `ak.stock_lh_yyb_control`，无入参。",
)
async def ths_lh_yyb_control() -> AkTableOut:
    try:
        df = await run_in_threadpool(ak.stock_lh_yyb_control)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_lh_yyb_control", {}, df)


# —— 新浪 龙虎 ——


@router.get(
    "/stock/sina/lhb/detail-daily",
    response_model=AkTableOut,
    summary="龙虎榜-每日详情（新浪）",
    description="封装 `ak.stock_lhb_detail_daily_sina`；`date` 为 YYYYMMDD。",
)
async def sina_lhb_detail_daily(
    date: str = Query("20240222", description="交易日 YYYYMMDD。例 20240222。"),
) -> AkTableOut:
    p = {"date": date}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_lhb_detail_daily_sina(date=date)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_lhb_detail_daily_sina", p, df)


@router.get(
    "/stock/sina/lhb/ggtj",
    response_model=AkTableOut,
    summary="龙虎榜-个股上榜统计（新浪）",
    description="封装 `ak.stock_lhb_ggtj_sina`；`symbol` 为时间窗口/区间标识。",
)
async def sina_lhb_ggtj(
    symbol: str = Query("5", description="如 5 表示 5 日 等。与官方示例一致。例 5。"),
) -> AkTableOut:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_lhb_ggtj_sina(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_lhb_ggtj_sina", p, df)


@router.get(
    "/stock/sina/lhb/yytj",
    response_model=AkTableOut,
    summary="龙虎榜-营业上榜统计（新浪）",
    description="封装 `ak.stock_lhb_yytj_sina`。",
)
async def sina_lhb_yytj(
    symbol: str = Query("5", description="如 5 等。与官方一致。"),
) -> AkTableOut:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_lhb_yytj_sina(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_lhb_yytj_sina", p, df)


@router.get(
    "/stock/sina/lhb/jgzz",
    response_model=AkTableOut,
    summary="龙虎榜-机构席位追踪（新浪）",
    description="封装 `ak.stock_lhb_jgzz_sina`。",
)
async def sina_lhb_jgzz(
    symbol: str = Query("5", description="如 5 等。与官方一致。"),
) -> AkTableOut:
    p = {"symbol": symbol}
    try:
        df = await run_in_threadpool(
            lambda: ak.stock_lhb_jgzz_sina(symbol=symbol)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_lhb_jgzz_sina", p, df)


@router.get(
    "/stock/sina/lhb/jgmx",
    response_model=AkTableOut,
    summary="龙虎榜-机构席位成交明细（新浪）",
    description="封装 `ak.stock_lhb_jgmx_sina`，无入参。",
)
async def sina_lhb_jgmx() -> AkTableOut:
    try:
        df = await run_in_threadpool(ak.stock_lhb_jgmx_sina)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ak("stock_lhb_jgmx_sina", {}, df)
