"""同花顺：行业概览与直连热榜。"""

from __future__ import annotations

import copy
from typing import Any

import akshare as ak
import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.api.deps import SettingsDep
from app.schemas.ak_openapi import ThsHotOut, ThsHotParamsEcho, ThsHotQueryDoc, ThsIndustrySummaryOut, field_desc
from app.schemas.ak_table import AkTableOut
from app.utils.ak_response import wrap_ak_dataframe, wrap_ak_table

_STOCK_DOC = "https://akshare.akfamily.xyz/data/stock/stock.html"
# 同花顺热榜/飙升 JSON，与 `…/v1/stock?stock_type=&type=&list_type=` 一致，固定写死不入配置
THS_HOT_STOCK_API = (
    "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock"
)
router = APIRouter(tags=["同花顺", "热榜", "行业"])


def _ak_table(name: str, params: dict, df) -> AkTableOut:
    return wrap_ak_table(name, params, df)


async def ths_hot_direct(
    settings: SettingsDep,
    stock_type: str,
    time_type: str,
    list_type: str,
    limit: int | None,
) -> ThsHotOut:
    """直连 `THS_HOT_STOCK_API`，与 GET 入参等价的查询为 `stock_type`·`type`·`list_type`。"""
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
            r = await client.get(THS_HOT_STOCK_API, params=params, headers=headers)
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

    return ThsHotOut(
        source="ths_direct",
        url=THS_HOT_STOCK_API,
        params=ThsHotParamsEcho.model_validate(
            {
                "stock_type": stock_type,
                "type": time_type,
                "list_type": list_type,
            }
        ),
        raw=raw_out,
        stock_list_total=total,
        stock_list_returned=(
            min(total, limit) if limit is not None else total
        ),
    )


@router.get(
    "/board/ths/industry-summary",
    response_model=ThsIndustrySummaryOut,
    summary="同花顺行业列表（概览）",
    description="封装 `ak.stock_board_industry_summary_ths`。",
)
async def ths_industry_board_summary() -> ThsIndustrySummaryOut:
    try:
        df = await run_in_threadpool(ak.stock_board_industry_summary_ths)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return wrap_ak_dataframe(ThsIndustrySummaryOut, "stock_board_industry_summary_ths", {}, df)


@router.get(
    "/board/ths/industry-index",
    response_model=AkTableOut,
    summary="同花顺-行业指数",
    description=(
        "封装 `ak.stock_board_industry_index_ths`；`symbol` 为行业名。"
        f"文档：[A 股数据]({_STOCK_DOC})。"
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
    return _ak_table("stock_board_industry_index_ths", p, df)


@router.get(
    "/hot/ths",
    response_model=ThsHotOut,
    summary="同花顺热榜（直连接口）",
    description=(
        "直连同花顺数据中心 JSON 接口。入参见 **Parameters**；出参中 `raw` 为上游完整 JSON，"
        "`params` 为已转发到同花顺的查询项（本服务不将 `limit` 写进上游，仅截断 `stock_list`）。"
    ),
)
async def tonghuashun_hot(
    settings: SettingsDep,
    stock_type: str = Query("a", description=field_desc(ThsHotQueryDoc, "stock_type")),
    time_type: str = Query(
        "hour",
        alias="type",
        description=field_desc(ThsHotQueryDoc, "time_type"),
    ),
    list_type: str = Query("normal", description=field_desc(ThsHotQueryDoc, "list_type")),
    limit: int | None = Query(
        None,
        ge=1,
        le=500,
        description=field_desc(ThsHotQueryDoc, "limit"),
    ),
) -> ThsHotOut:
    return await ths_hot_direct(
        settings, stock_type, time_type, list_type, limit
    )


@router.get(
    "/hot/ths/popularity",
    response_model=ThsHotOut,
    summary="同花顺人气榜（直连接口）",
    description=(
        "固定请求等价于同花顺 URL："
        "`stock_type=a`（A 股）·`type=hour`·`list_type=normal`；"
        "即人气榜/常规热股列表。可选 `limit` 仅本服务截断 `stock_list`。"
    ),
)
async def ths_hot_popularity(
    settings: SettingsDep,
    limit: int | None = Query(
        None,
        ge=1,
        le=500,
        description=field_desc(ThsHotQueryDoc, "limit"),
    ),
) -> ThsHotOut:
    return await ths_hot_direct(settings, "a", "hour", "normal", limit)


@router.get(
    "/hot/ths/skyrocket",
    response_model=ThsHotOut,
    summary="同花顺人气飙升榜（直连接口）",
    description=(
        "固定请求等价于同花顺 URL："
        "`stock_type=a`·`type=hour`·`list_type=skyrocket`；"
        "即人气飙升榜。可选 `limit` 仅本服务截断 `stock_list`。"
    ),
)
async def ths_hot_skyrocket(
    settings: SettingsDep,
    limit: int | None = Query(
        None,
        ge=1,
        le=500,
        description=field_desc(ThsHotQueryDoc, "limit"),
    ),
) -> ThsHotOut:
    return await ths_hot_direct(settings, "a", "hour", "skyrocket", limit)
