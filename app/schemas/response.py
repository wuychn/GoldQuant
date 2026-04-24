"""统一 API 响应包装。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Response(BaseModel):
    """标准 JSON 响应，业务载荷在 `data`。"""

    code: int = Field(0, description="业务状态码，0 表示成功。")
    message: str = Field("success", description="提示信息。")
    data: Any = Field(default=None, description="业务数据。")
