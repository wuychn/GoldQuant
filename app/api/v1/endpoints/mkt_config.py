"""管理端：部分第三方站点可注入的请求头（示例：写入项目根 `.eastmoney.header` / `.ths.header`）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.eastmoney_headers import eastmoney_header_file_path, save_headers_to_file
from app.core.ths_headers import save_ths_headers_to_file, ths_header_file_path
from app.schemas.response import Response

router = APIRouter(prefix="/admin/eastmoney", tags=["admin"])
ths_router = APIRouter(prefix="/admin/ths", tags=["admin"])


class EastmoneyHeaderItem(BaseModel):
    key: str = Field(..., min_length=1, description="HTTP 头名称")
    value: str = Field(..., description="HTTP 头值")


@router.post(
    "/headers",
    summary="设置东财出站请求头",
    description=(
        "请求体为 JSON 数组，元素为 `{ \"key\", \"value\" }`。"
        "整份配置会覆盖写入项目根目录下的 `.eastmoney.header`。"
        "已对 `requests` 打补丁：访问 **eastmoney.com** 时会从该文件读取并合并到请求头。"
    ),
    response_model=Response,
)
async def set_eastmoney_headers(
    items: list[EastmoneyHeaderItem],
) -> Response:
    try:
        payload = [item.model_dump() for item in items]
        save_headers_to_file(payload)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    path = eastmoney_header_file_path()
    return Response(
        data={
            "ok": True,
            "path": str(path),
            "count": len(payload),
        }
    )


class ThsHeaderItem(BaseModel):
    key: str = Field(..., min_length=1, description="HTTP 头名称")
    value: str = Field(..., description="HTTP 头值")


@ths_router.post(
    "/headers",
    summary="设置同花顺直连请求头",
    description=(
        "请求体为 JSON 数组，元素为 `{ \"key\", \"value\" }`（与东财接口相同）。"
        "整份配置覆盖写入项目根目录下的 `.ths.header`。"
        "哪些 URL 会合并该文件中的头，由 `app.core.ths_headers` 内代码常量 `_THS_HEADER_URL_MARKERS` 决定。"
        "本服务内 `httpx` 直连同花顺（`ths_util.call_ths_api`、`/hot/ths*`）会在发请求前读取并合并。"
    ),
    response_model=Response,
)
async def set_ths_headers(
    items: list[ThsHeaderItem],
) -> Response:
    try:
        payload = [item.model_dump() for item in items]
        save_ths_headers_to_file(payload)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    path = ths_header_file_path()
    return Response(
        data={
            "ok": True,
            "path": str(path),
            "count": len(payload),
        }
    )
