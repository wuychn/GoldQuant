"""GoldQuant 数据 API 入口。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.router import api_router
from common.config import Settings, get_settings
from app.scheduling.quant_scheduler import build_quant_scheduler, shutdown_quant_scheduler
from app.core.exception_handlers import (
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from common.utils.source_headers import apply_source_header_patch
from app.core.proxy import apply_process_proxy
from common.utils.error_log import configure_app_logging, log_caught_error
from common.progress_log import configure_progress_logging, log_progress


logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """应用工厂，便于测试与多实例配置。"""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # 对 requests 打补丁：东财域名合并 `.eastmoney.header`（须在首次调用 akshare 前）
        configure_app_logging()
        apply_source_header_patch()
        configure_progress_logging()
        # 写入 HTTP_PROXY / HTTPS_PROXY，供 AKShare（requests/curl_cffi）等出站请求使用
        apply_process_proxy(settings)
        sched = None
        try:
            sched = build_quant_scheduler()
        except Exception as e:
            log_caught_error(logger, "量化定时任务构建失败，服务继续运行但不会自动跑 quant 流水线", e)
        if sched is not None:
            sched.start()
            log_progress("api", "量化定时任务调度器已启动")
        yield
        if sched is not None:
            shutdown_quant_scheduler(sched)

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

    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

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
