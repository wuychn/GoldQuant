"""将异常统一序列化为 `Response` 结构的 JSON。"""

from __future__ import annotations

import json
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.schemas.response import Response


def _detail_message(detail: Any) -> str:
    if detail is None:
        return ""
    if isinstance(detail, str):
        return detail
    try:
        return json.dumps(detail, ensure_ascii=False)
    except TypeError:
        return str(detail)


def _validation_summary(errors: list[Any]) -> str:
    if not errors:
        return "请求参数校验失败"
    e0 = errors[0]
    loc = e0.get("loc") or ()
    loc_s = " → ".join(str(x) for x in loc)
    msg = str(e0.get("msg", ""))
    if loc_s:
        return f"{loc_s}: {msg}"
    return msg or "请求参数校验失败"


async def http_exception_handler(
    _request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    body = Response(
        code=exc.status_code,
        message=_detail_message(exc.detail),
        data=None,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=body.model_dump(),
    )


async def validation_exception_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    errors = exc.errors()
    body = Response(
        code=422,
        message=_validation_summary(errors),
        data=jsonable_encoder(errors),
    )
    return JSONResponse(status_code=422, content=body.model_dump())


async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    settings = get_settings()
    hide = settings.ENV.lower() in ("production", "prod")
    message = "服务器内部错误" if hide else str(exc)
    body = Response(code=500, message=message, data=None)
    return JSONResponse(status_code=500, content=body.model_dump())
