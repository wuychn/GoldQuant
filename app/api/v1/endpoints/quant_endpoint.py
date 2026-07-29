"""openclaw量化数据入口 — 薄路由，逻辑在 quant.services.market。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks
from fastapi.concurrency import run_in_threadpool

from app.api.deps import SettingsDep
from app.schemas.response import Response
from quant.services.market.payload import (
    build_during_market_payload,
    build_evening_payload,
    build_lunch_payload,
    build_news_payload,
    build_pre_market_payload,
)
from quant.services.market.intraday import run_intraday_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["量化入口"])


def _public_payload(payload: dict) -> dict:
    out = dict(payload)
    out.pop("_session", None)
    return out


@router.get("/quant/market/news", response_model=Response, summary="新闻")
async def news(settings: SettingsDep) -> Response:
    br = await build_news_payload(settings)
    data = br.payload if isinstance(br.payload, dict) else {"news": br.payload}
    return Response(data=_public_payload(data) if isinstance(data, dict) else data)


@router.get("/quant/market/pre_market", response_model=Response, summary="盘前")
async def pre_market(settings: SettingsDep, background_tasks: BackgroundTasks) -> Response:
    br = await build_pre_market_payload(settings)
    return Response(data=_public_payload(br.payload))


@router.get("/quant/market/during_market", response_model=Response, summary="盘中")
async def during_market(settings: SettingsDep, background_tasks: BackgroundTasks) -> Response:
    session = await run_in_threadpool(run_intraday_session)
    br = await build_during_market_payload(settings, session=session)
    return Response(data=_public_payload(br.payload))


@router.get("/quant/market/post_market_lunch", response_model=Response, summary="午间复盘")
async def post_market_lunch(settings: SettingsDep) -> Response:
    br = await build_lunch_payload(settings)
    return Response(data=_public_payload(br.payload))


@router.get("/quant/market/post_market_evening", response_model=Response, summary="晚间复盘")
async def post_market_evening(settings: SettingsDep, background_tasks: BackgroundTasks) -> Response:
    br = await build_evening_payload(settings)
    return Response(data=_public_payload(br.payload))
