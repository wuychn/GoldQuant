"""聚合 v1 路由。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import data, eastmoney_config

api_router = APIRouter()
api_router.include_router(data.router)
api_router.include_router(eastmoney_config.router)
