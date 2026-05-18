"""量化大盘接口的附加数据：涨停池、盘前快照、市场状态机等；返回字段均为中文键。"""

from __future__ import annotations

import logging
from typing import Any

import akshare as ak
import pandas as pd

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
            end_time="09:25:00",
        )
        if df is None or df.empty:
            return []
        return dataframe_to_records(df)
    except Exception:
        logger.exception("集合竞价分钟失败 [%s] symbol=%s", context, symbol)
        return None


def today_zt_pool_full_zh(context: str) -> list[dict[str, Any]] | None:
    """东财当日涨停股池（不过滤连板），列名为中文。TODO 与dfcf_util的ztgc合并"""
    try:
        df = ak.stock_zt_pool_em(date=today())
        if df is None or df.empty:
            return []
        return dataframe_to_records(df)
    except Exception:
        logger.exception("今日涨停股池全量失败 [%s]", context)
        return None


def previous_zt_pool_zh(context: str, trade_date: str) -> list[dict[str, Any]] | None:
    """东财指定交易日的昨日涨停股池（接口名如此），列名为中文。"""
    try:
        df = ak.stock_zt_pool_previous_em(date=trade_date)
        if df is None or df.empty:
            return []
        return dataframe_to_records(df)
    except Exception:
        logger.exception("昨日涨停股池失败 [%s] date=%s", context, trade_date)
        return None


def _index_ma20_vs_close_pct(context: str) -> dict[str, Any | None]:
    """上证指数收盘相对 20 日均线幅度（%）；日线来源东财指数接口。"""
    out: dict[str, Any | None] = {
        "指数代码": "sh000001",
        "指数名称": "上证指数",
        "最新收盘": None,
        "20日均线": None,
        "收盘较20日均线": None,
    }
    try:
        start = get_n_workdays_ago(n=60) or "20200101"
        df = ak.stock_zh_index_daily_em(symbol="sh000001", start_date=start, end_date=today())
        if df is None or df.empty or len(df) < 20:
            return out
        closes = pd.to_numeric(df["close"], errors="coerce").dropna()
        if len(closes) < 20:
            return out
        last = float(closes.iloc[-1])
        ma20 = float(closes.tail(20).mean())
        out["最新收盘"] = round(last, 4)
        out["20日均线"] = round(ma20, 4)
        if ma20:
            out["收盘较20日均线"] = round((last / ma20 - 1.0) * 100.0, 4)
        return out
    except Exception:
        logger.exception("上证指数20日均线失败 [%s]", context)
        return out


def _two_market_volume_ratio(context: str, index_spot: list[dict[str, Any]] | None) -> dict[str, Any | None]:
    """从实时指数行情中提取上证、深证量比，取平均作为两市成交额倍率（量比已按时间归一化）。"""
    out: dict[str, Any | None] = {
        "上证量比": None,
        "深证量比": None,
        "今日相对近5日均倍率": None,
    }
    if not index_spot:
        return out
    sh_vr, sz_vr = None, None
    for item in index_spot:
        code = str(item.get("代码", "") or "").strip()
        name = str(item.get("名称", "") or "").strip()
        vr = item.get("量比")
        if vr is None:
            continue
        try:
            vr_f = float(vr)
        except (TypeError, ValueError):
            continue
        if code == "000001" or "上证" in name:
            sh_vr = vr_f
        elif code == "399001" or "深证成" in name:
            sz_vr = vr_f
    out["上证量比"] = round(sh_vr, 4) if sh_vr is not None else None
    out["深证量比"] = round(sz_vr, 4) if sz_vr is not None else None
    # 取两市均值作为综合倍率
    values = [v for v in (sh_vr, sz_vr) if v is not None]
    if values:
        out["今日相对近5日均倍率"] = round(sum(values) / len(values), 4)
    return out


def _yesterday_zt_pool_performance_zh(context: str, prev_trade_date: str | None) -> dict[str, Any | None]:
    """上一交易日涨停股池当日涨跌幅中位数（%，近似「昨日涨停表现」）。"""
    out: dict[str, Any | None] = {
        "数据日期": prev_trade_date,
        "样本数量": None,
        "涨跌幅中位数": None,
        "涨跌幅均值": None,
    }
    if not prev_trade_date:
        return out
    rows = previous_zt_pool_zh(context, prev_trade_date)
    if not rows:
        return out
    vals: list[float] = []
    for r in rows:
        v = r.get("涨跌幅")
        try:
            if v is not None and v != "":
                vals.append(float(v))
        except (TypeError, ValueError):
            continue
    if not vals:
        return out
    s = pd.Series(vals)
    out["样本数量"] = len(vals)
    out["涨跌幅中位数"] = round(float(s.median()), 4)
    out["涨跌幅均值"] = round(float(s.mean()), 4)
    return out


def _zt_height_and_count_zh(context: str, pool: list[dict[str, Any]] | None) -> dict[str, Any | None]:
    out: dict[str, Any | None] = {"涨停家数": None, "市场最高连板数": None}
    if not pool:
        return out
    out["涨停家数"] = len(pool)
    mx = 0
    for r in pool:
        v = r.get("连板数")
        try:
            if v is not None and v != "":
                mx = max(mx, int(float(v)))
        except (TypeError, ValueError):
            continue
    out["市场最高连板数"] = mx if mx > 0 else None
    return out


def build_market_state_machine_zh(
    context: str,
    *,
    zt_pool_full: list[dict[str, Any]] | None = None,
    realtime_index_spot: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """策略 §7.1 状态机用到的可自动计算项，键名均为中文。"""
    prev_td = get_n_workdays_ago(n=1)
    pool_full = zt_pool_full if zt_pool_full is not None else today_zt_pool_full_zh(context)
    idx = _index_ma20_vs_close_pct(context)
    amt = _two_market_volume_ratio(context, realtime_index_spot)
    # 数据有延迟，需确认 TODO
    ztp = _yesterday_zt_pool_performance_zh(context, prev_td)
    ztc = _zt_height_and_count_zh(context, pool_full)

    # 今日大盘实时涨跌幅（从实时指数行情中提取上证涨跌幅）
    realtime = _extract_realtime_index_change(realtime_index_spot)

    return {
        "上证指数": idx,
        "两市成交额近似": amt,
        "昨日涨停表现": ztp,
        "今日涨停统计": {
            "涨停家数": ztc.get("涨停家数") if ztc else None,
            "市场最高连板数": ztc.get("市场最高连板数") if ztc else None,
        },
        "今日大盘实时": realtime,
    }


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
