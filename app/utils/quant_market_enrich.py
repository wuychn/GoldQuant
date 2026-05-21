"""量化大盘接口的附加数据：涨停池、盘前快照、市场状态机等；返回字段均为中文键。"""

from __future__ import annotations

import logging
from typing import Any

import akshare as ak

from app.utils.common_util import get_n_workdays_ago, today
from app.utils.dataframe import dataframe_to_records

logger = logging.getLogger(__name__)

_SPOT_KEYS = (
    "名称",
    "最新价",
    "涨跌幅",
    "涨跌额",
    "今开",
    "最高",
    "最低",
    "昨收",
    "量比",
    "换手率",
    "成交额",
    "成交量",
    "振幅",
)


def spot_snapshot_for_codes(context: str, codes: set[str]) -> dict[str, dict[str, Any]]:
    """东财 A 股实时全表（``stock_zh_a_spot_em``）按代码筛选子集；codes 为 6 位数字不含前缀。

    注意：全表体积大，频繁调用易触发源站限流；盘前聚合默认不调用（见 ``Settings.QUANT_SPOT_EM_FULL_TABLE``）。
    """
    if not codes:
        return {}
    try:
        df = ak.stock_zh_a_spot_em()
    except Exception:
        logger.exception("盘前实时快照拉取失败 [%s]", context)
        return {}
    try:
        records = dataframe_to_records(df)
    except Exception:
        logger.exception("盘前实时快照转表失败 [%s]", context)
        return {}
    out: dict[str, dict[str, Any]] = {}
    for r in records:
        c = str(r.get("代码", "")).strip()
        if c in codes:
            out[c] = {kk: r.get(kk) for kk in _SPOT_KEYS}
    return out


def pre_auction_minute_zh(context: str, symbol: str) -> list[dict[str, Any]] | None:
    """东财盘前竞价分钟（09:15–09:25），列名与东财一致为中文。"""
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





def _extract_realtime_index_change(index_spot: list[dict[str, Any]] | None) -> dict[str, Any]:
    """从实时指数行情中提取上证指数当日涨跌幅。"""
    out: dict[str, Any] = {"涨跌幅": None}
    if not index_spot:
        return out
    for item in index_spot:
        code = str(item.get("代码", "") or item.get("code", "")).strip()
        name = str(item.get("名称", "") or "").strip()
        if code == "000001" or "上证" in name:
            chg = item.get("涨跌幅")
            if chg is not None:
                try:
                    out["涨跌幅"] = round(float(chg), 2)
                except (TypeError, ValueError):
                    pass
            break
    return out
