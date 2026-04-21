"""股票热度、资讯、板块与第三方热榜接口。"""

from __future__ import annotations

import copy
from enum import Enum
from typing import Any

import akshare as ak
import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.api.deps import SettingsDep
from app.utils.dataframe import dataframe_to_records

router = APIRouter(tags=["data"])


@router.get(
    "/hot/eastmoney/surge",
    summary="东方财富飙升榜（A股）",
    description=(
            "封装 AKShare：`stock_hot_up_em`（个股人气榜-飙升榜）。"
            "文档：[飙升榜-A股](https://akshare.akfamily.xyz/data/stock/stock.html#飙升榜-a股)。"
    ),
)
async def eastmoney_surge() -> dict[str, Any]:
    try:
        df = await run_in_threadpool(ak.stock_hot_up_em)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    rows = dataframe_to_records(df)
    return {
        "source": "akshare.stock_hot_up_em",
        "row_count": len(rows),
        "rows": rows,
    }


@router.get(
    "/hot/eastmoney/popularity",
    summary="东方财富个股人气榜（A股，前100）",
    description=(
            "封装 AKShare：`stock_hot_rank_em`（人气榜-A股）。"
            "文档：[人气榜-A股](https://akshare.akfamily.xyz/data/stock/stock.html#人气榜-a股)。"
    ),
)
async def eastmoney_popularity() -> dict[str, Any]:
    try:
        df = await run_in_threadpool(ak.stock_hot_rank_em)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    rows = dataframe_to_records(df)
    return {
        "source": "akshare.stock_hot_rank_em",
        "row_count": len(rows),
        "rows": rows,
    }


@router.get(
    "/news/em",
    summary="东方财富个股资讯",
    description=(
            "封装 AKShare：`stock_news_em`。"
            "文档：[个股新闻](https://akshare.akfamily.xyz/data/stock/stock.html#个股新闻)。"
    ),
)
async def eastmoney_news(
        symbol: str = Query(
            ...,
            description="股票代码或搜索关键词，例如 603777",
            examples=["603777"],
        ),
) -> dict[str, Any]:
    try:
        df = await run_in_threadpool(ak.stock_news_em, symbol)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    rows = dataframe_to_records(df)
    return {
        "source": "akshare.stock_news_em",
        "symbol": symbol,
        "row_count": len(rows),
        "rows": rows,
    }


@router.get(
    "/stock/em/individual-info",
    summary="个股信息查询（东财）",
    description=(
            "封装 AKShare：`stock_individual_info_em`（东方财富-个股-股票信息）。"
            "文档：[个股信息查询-东财](https://akshare.akfamily.xyz/data/stock/stock.html#个股信息查询-东财)。"
    ),
)
async def eastmoney_individual_info(
        symbol: str = Query(
            ...,
            description="股票代码，如 000001、600000",
            examples=["000001"],
        ),
) -> dict[str, Any]:
    try:
        df = await run_in_threadpool(ak.stock_individual_info_em, symbol)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    rows = dataframe_to_records(df)
    return {
        "source": "akshare.stock_individual_info_em",
        "symbol": symbol,
        "row_count": len(rows),
        "rows": rows,
    }


@router.get(
    "/board/ths/industry-summary",
    summary="同花顺行业列表（概览）",
    description=(
            "封装 AKShare：`stock_board_industry_summary_ths`（同花顺-行业板块-行业一览）。"
            "文档见 AKShare 股票数据-同花顺相关接口。"
    ),
)
async def ths_industry_board_summary() -> dict[str, Any]:
    try:
        df = await run_in_threadpool(ak.stock_board_industry_summary_ths)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    rows = dataframe_to_records(df)
    return {
        "source": "akshare.stock_board_industry_summary_ths",
        "row_count": len(rows),
        "rows": rows,
    }


@router.get(
    "/hot/ths",
    summary="同花顺热榜（直连接口）",
    description=(
            "直连同花顺数据中心 JSON 接口（非 AKShare 封装）。"
            "默认参数对应文档示例：A 股、小时榜、普通列表。"
    ),
)
async def tonghuashun_hot(
        settings: SettingsDep,
        stock_type: str = Query("a", description="市场类型，如 a 表示 A 股"),
        time_type: str = Query(
            "hour",
            alias="type",
            description="时间粒度，与上游参数名 `type` 一致，如 hour",
        ),
        list_type: str = Query("normal", description="列表类型，如 normal"),
        limit: int | None = Query(
            None,
            ge=1,
            le=500,
            description="可选：仅截取前 N 条 stock_list（默认返回接口全部数据）",
        ),
) -> dict[str, Any]:
    params = {
        "stock_type": stock_type,
        "type": time_type,
        "list_type": list_type,
    }
    headers = {
        "User-Agent": settings.THS_DEFAULT_USER_AGENT,
        "Accept": "application/json",
    }
    client_kw: dict[str, Any] = {"timeout": settings.HTTP_CLIENT_TIMEOUT}
    if px := settings.httpx_proxy_url():
        client_kw["proxy"] = px
    try:
        async with httpx.AsyncClient(**client_kw) as client:
            r = await client.get(settings.THS_HOT_URL, params=params, headers=headers)
            r.raise_for_status()
            payload = r.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"响应非 JSON: {exc}") from exc

    raw_out: Any = copy.deepcopy(payload)
    total = 0
    if isinstance(raw_out, dict):
        inner = raw_out.get("data")
        if isinstance(inner, dict) and "stock_list" in inner:
            sl = inner.get("stock_list")
            if isinstance(sl, list):
                total = len(sl)
                if limit is not None:
                    inner["stock_list"] = sl[:limit]

    return {
        "source": "ths_direct",
        "url": settings.THS_HOT_URL,
        "params": params,
        "raw": raw_out,
        "stock_list_total": total,
        "stock_list_returned": (
            min(total, limit) if limit is not None else total
        ),
    }
