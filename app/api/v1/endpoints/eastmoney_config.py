"""东财自定义请求头配置（写入项目根 `.eastmoney.header`）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.eastmoney_headers import eastmoney_header_file_path, save_headers_to_file

router = APIRouter(prefix="/admin/eastmoney", tags=["admin"])


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
)
async def set_eastmoney_headers(items: list[EastmoneyHeaderItem]) -> dict[str, Any]:
    try:
        payload = [item.model_dump() for item in items]
        save_headers_to_file(payload)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    path = eastmoney_header_file_path()
    return {
        "ok": True,
        "path": str(path),
        "count": len(payload),
    }
