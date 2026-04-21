"""GoldQuant 数据 API 入口。"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.eastmoney_headers import apply_eastmoney_requests_patch
from app.core.proxy import apply_process_proxy


def create_app(settings: Settings | None = None) -> FastAPI:
    """应用工厂，便于测试与多实例配置。"""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # 对 requests 打补丁：东财域名合并 `.eastmoney.header`（须在首次调用 akshare 前）
        apply_eastmoney_requests_patch()
        # 写入 HTTP_PROXY / HTTPS_PROXY，供 AKShare（requests/curl_cffi）等出站请求使用
        apply_process_proxy(get_settings())
        yield

    app = FastAPI(
        title=settings.PROJECT_NAME,
        description=settings.PROJECT_DESCRIPTION,
        version=settings.VERSION,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins(),
        allow_credentials=settings.cors_effective_credentials(),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix=settings.API_V1_STR)

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", tags=["meta"])
    def root() -> dict[str, str]:
        return {
            "service": settings.PROJECT_NAME,
            "version": settings.VERSION,
            "env": settings.ENV,
            "docs": "/docs",
            "openapi": "/openapi.json",
            "health": "/health",
            "api_v1": settings.API_V1_STR,
        }

    return app


app = create_app()
