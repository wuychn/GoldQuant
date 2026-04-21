"""将 pandas DataFrame 转为可 JSON 序列化的行列表。"""

from __future__ import annotations

import json

import pandas as pd


def dataframe_to_records(df: pd.DataFrame) -> list[dict]:
    """使用 pandas JSON 导出，统一处理 NaN 与日期。"""
    if df.empty:
        return []
    return json.loads(df.to_json(orient="records", date_format="iso"))
