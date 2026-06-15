"""量化个股分钟行情（东财）；返回字段以东财中文列为主。"""

from __future__ import annotations

import logging
from typing import Any

import akshare as ak

from app.utils.dataframe import dataframe_to_records
from app.utils.error_log import log_caught_error

logger = logging.getLogger(__name__)


def stock_intraday_minute_zh(context: str, symbol: str) -> list[dict[str, Any]] | None:
    """东财当日 1 分钟 K（09:15–15:00），含集合竞价与连续竞价；列名与东财一致为中文。"""
    try:
        df = ak.stock_zh_a_hist_pre_min_em(
            symbol=str(symbol).strip(),
            start_time="09:15:00",
            end_time="15:00:00",
        )
        if df is None or df.empty:
            return []
        return dataframe_to_records(df)
    except Exception as e:
        log_caught_error(logger, f"分钟行情 [{context}] symbol={symbol}", e)
        return None


def pre_auction_minute_zh(context: str, symbol: str) -> list[dict[str, Any]] | None:
    """兼容旧名；实为 ``stock_intraday_minute_zh``。"""
    return stock_intraday_minute_zh(context, symbol)
