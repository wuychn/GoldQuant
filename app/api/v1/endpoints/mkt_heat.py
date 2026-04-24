"""全市场/个股人气：榜单聚合（AKShare）与可配置直连接口（JSON 热表）。"""

from __future__ import annotations

import copy
from typing import Any

import akshare as ak
import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.api.deps import SettingsDep
from app.schemas.ak_openapi import (
    EmPopularityOut,
    EmSurgeOut,
    ThsHotOut,
    ThsHotParamsEcho,
    ThsHotQueryDoc,
    field_desc,
)
from app.schemas.response import Response
from app.utils.ak_response import wrap_ak_dataframe

_BASE = "https://akshare.akfamily.xyz/data/stock/stock.html"
# 热股 JSON 根路径（与 `…/v1/stock?stock_type=&type=&list_type=` 一致），本模块写死
HOT_STOCK_LIST_API = (
    "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock"
)
router = APIRouter(tags=["热度", "人气", "热榜"])


@router.get(
    "/hot/eastmoney/surge",
    response_model=Response,
    summary="A 股飙升榜",
    description=(
        "封装 `ak.stock_hot_up_em`（个股人气榜-飙升榜）。\n\n"
        f"文档：[飙升榜]({_BASE}#飙升榜-a股)。\n"
    ),
)
async def eastmoney_surge() -> Response:
    try:
        df = await run_in_threadpool(ak.stock_hot_up_em)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(
        data=wrap_ak_dataframe(EmSurgeOut, "stock_hot_up_em", {}, df)
    )


@router.get(
    "/hot/eastmoney/popularity",
    response_model=Response,
    summary="A 股个股人气榜（约前 100）",
    description=(
        "封装 `ak.stock_hot_rank_em`。\n\n"
        f"文档：[人气榜]({_BASE}#人气榜-a股)。"
    ),
)
async def eastmoney_popularity() -> Response:
    try:
        df = await run_in_threadpool(ak.stock_hot_rank_em)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(
        data=wrap_ak_dataframe(EmPopularityOut, "stock_hot_rank_em", {}, df)
    )


async def hot_list_direct(
    settings: SettingsDep,
    stock_type: str,
    time_type: str,
    list_type: str,
    limit: int | None,
) -> ThsHotOut:
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
            r = await client.get(HOT_STOCK_LIST_API, params=params, headers=headers)
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
        url=HOT_STOCK_LIST_API,
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
    "/hot/ths",
    response_model=Response,
    summary="热股列表（直连接口，可配 list_type 等）",
    description=(
        "直连热股 JSON。`params` 为转发到上游的查询子集；`limit` 仅截断本服务返回的 `stock_list`。"
    ),
)
async def hot_list_flexible(
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
) -> Response:
    return Response(
        data=await hot_list_direct(
            settings, stock_type, time_type, list_type, limit
        )
    )


@router.get(
    "/hot/ths/popularity",
    response_model=Response,
    summary="热股-人气榜（直连接口）",
    description=(
        "固定查询：`stock_type=a`·`type=hour`·`list_type=normal`。"
        "可选 `limit` 仅截断 `stock_list`。"
    ),
)
async def hot_list_popularity(
    settings: SettingsDep,
    limit: int | None = Query(
        None,
        ge=1,
        le=500,
        description=field_desc(ThsHotQueryDoc, "limit"),
    ),
) -> Response:
    return Response(
        data=await hot_list_direct(settings, "a", "hour", "normal", limit)
    )


@router.get(
    "/hot/ths/skyrocket",
    response_model=Response,
    summary="热股-人气飙升榜（直连接口）",
    description=(
        "固定查询：`stock_type=a`·`type=hour`·`list_type=skyrocket`。"
    ),
)
async def hot_list_skyrocket(
    settings: SettingsDep,
    limit: int | None = Query(
        None,
        ge=1,
        le=500,
        description=field_desc(ThsHotQueryDoc, "limit"),
    ),
) -> Response:
    return Response(
        data=await hot_list_direct(settings, "a", "hour", "skyrocket", limit)
    )
