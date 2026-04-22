"""无逐列 Pydantic 时使用的通用表格式出参（OpenAPI 仍展示表结构与 params）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AkTableOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(
        ..., description="底层 `akshare.xxx` 函数全名。",
    )
    params: dict[str, Any] = Field(
        ...,
        description="本次实际传入 AKShare 的入参，与 **Parameters** 中各 Query 一一对应。",
    )
    row_count: int
    columns: list[str] = Field(
        ...,
        description="DataFrame 列名，顺序与上游一致；`rows` 中每行对象的键同此。",
    )
    rows: list[dict[str, Any]] = Field(
        ...,
        description="行数据。各列数据类型、单位以 AKShare 该接口在官网文档的「输出参数」为准。",
    )
