"""将 AKShare 返回的 DataFrame 转为统一 API 出参。"""

from __future__ import annotations

from typing import Any, TypeVar, cast

import pandas as pd
from pydantic import BaseModel

from app.schemas.ak_table import AkTableOut
from app.utils.dataframe import dataframe_to_records

TOut = TypeVar("TOut", bound=BaseModel)


def ak_dataframe_to_payload(
    source_func: str, params: dict[str, Any], df: pd.DataFrame
) -> dict[str, Any]:
    """
    :param source_func: 不含 `akshare.` 前缀的函数名，如 `stock_zh_a_hist`。
    :param params: 与本次调用一致的入参，用于回显与排错。
    """
    rows = dataframe_to_records(df)
    return {
        "source": f"akshare.{source_func}",
        "params": params,
        "row_count": len(rows),
        "columns": list(df.columns) if not df.empty else [],
        "rows": rows,
    }


def wrap_ak_dataframe(
    out_type: type[TOut],
    source_stem: str,
    params: BaseModel | dict[str, Any],
    df: pd.DataFrame,
) -> TOut:
    """
    将 `ak_dataframe_to_payload` 的结果与入参 Pydantic 对象组装为强类型 OpenAPI 出参。

    若 `params` 为 `BaseModel` 则响应中 `params` 节保留该结构（与 Swagger 中各字段说明一致）；
    为 `dict` 时原样回显（如无查询入参的 `{}`）。
    """
    p_dict = params if isinstance(params, dict) else params.model_dump()
    body = ak_dataframe_to_payload(source_stem, p_dict, df)
    if not isinstance(params, dict):
        body["params"] = params
    return cast(TOut, out_type.model_validate(body))


def wrap_ak_table(
    source_stem: str, params: dict[str, Any], df: pd.DataFrame
) -> AkTableOut:
    """无逐行 Pydantic 时的统一表格式出参，供批量 AKShare 封装复用。"""
    return AkTableOut.model_validate(ak_dataframe_to_payload(source_stem, params, df))
