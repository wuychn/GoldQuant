"""量化大盘附加数据（如盘前竞价分钟行情）；返回字段以东财中文列为主。"""

from __future__ import annotations

import logging
from typing import Any

import akshare as ak

from app.utils.dataframe import dataframe_to_records

logger = logging.getLogger(__name__)


def pre_auction_minute_zh(context: str, symbol: str) -> list[dict[str, Any]] | None:
    """东财盘前竞价分钟（默认 09:15–当日收盘时段），列名与东财一致为中文。"""
    try:
        df = ak.stock_zh_a_hist_pre_min_em(
            symbol=str(symbol).strip(),
            start_time="09:15:00",
            end_time="15:00:00",
        )
        if df is None or df.empty:
            return []
        return dataframe_to_records(df)
    except Exception:
        logger.exception("集合竞价分钟失败 [%s] symbol=%s", context, symbol)
        return None
