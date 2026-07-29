"""Market data tools: indices, zqxy, zt, hot (no enrich — see services/market/enrich)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi.concurrency import run_in_threadpool

from quant.data.calendar import prev_trading_day
from quant.data.sources.akshare.fund_flow import fetch_market_fund_flow_last
from quant.data.sources.eastmoney import ztgc, ztgc_with_date
from quant.data.sources.etf52.zdfb import zdfb_52etf
from quant.data.sources.rate_limit import alimit
from quant.data.tools.payload_utils import merge_concept_boards, merge_industry_boards, zt_height
from quant.pool.sources import prefilter_popularity
from quant.pool.symbol_filter import apply_symbol_pool_filter
from common.progress_log import log_progress
from common.timeutil import cn_now

logger = logging.getLogger(__name__)


def log_tool_error(context: str, exc: Exception | None = None) -> None:
    if exc is not None:
        logger.warning("量化数据 [%s]: %s", context, exc, exc_info=exc)
    else:
        logger.warning("量化数据 [%s]", context)


async def fetch_index_spot() -> list | None:
    from quant.data.sources.factory import get_index_source

    try:
        return get_index_source().fetch_hs_important()
    except Exception as e:
        log_tool_error("大盘指数 | ak.stock_zh_index_spot_em", e)
        return None


async def fetch_zqxy(*, market_phase: str = "intraday") -> Any:
    from quant.data.sources.ths import zdfb_ths, zdfb_v2_realtime

    try:
        return await alimit("ths", lambda: zdfb_v2_realtime())
    except Exception:
        log_tool_error("赚钱效应 | 同花顺V2接口")
    try:
        return await zdfb_52etf(market_phase=market_phase)
    except Exception:
        log_tool_error("赚钱效应 | 52etf涨跌分布")
    try:
        return await alimit("ths", lambda: zdfb_ths())
    except Exception:
        log_tool_error("赚钱效应 | 同花顺涨跌分布")
    return None


def _apply_row_limit(rows: list | None, limit: int | None) -> list:
    if not isinstance(rows, list):
        return []
    allowed = apply_symbol_pool_filter(rows)
    if limit is None:
        return allowed
    return allowed[:limit]


def ztgk_rows(settings: Any, zt_full: list, *, more: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {}
    row_limit = settings.quant_bulk_row_limit()
    zt_allowed = apply_symbol_pool_filter(zt_full)
    height = zt_height(zt_allowed)
    result["今日涨停"] = zt_allowed[:row_limit] if row_limit is not None else zt_allowed
    result["市场高度"] = f"{height}连板"
    if more:
        try:
            prev = prev_trading_day(cn_now().date())
            if prev is None:
                raise ValueError("无上一交易日")
            zrzt = ztgc_with_date(prev.strftime("%Y%m%d"))
            result["昨日涨停"] = _apply_row_limit(zrzt, row_limit)
        except Exception:
            log_tool_error("昨日涨停股池全量 | ztgc_with_date")
    return result


async def fetch_ztgk(settings: Any, more: bool = False, *, zt_full: list | None = None):
    try:
        pool = zt_full if zt_full is not None else await run_in_threadpool(ztgc)
        return ztgk_rows(settings, pool if isinstance(pool, list) else [], more=more)
    except Exception as e:
        log_tool_error("今日涨停股全量 | ztgc", e)
        return {}


async def fetch_hot(settings: Any, *, progress_scope: str | None = "during_market") -> list:
    from quant.data.sources.ths import hot_stock

    try:
        n = settings.quant_hot_list_limit()

        async def _call():
            return await hot_stock(n)

        raw_hot = await alimit("ths", _call)
        rows = prefilter_popularity(raw_hot if isinstance(raw_hot, list) else [])
        scope = progress_scope or "during_market"
        log_progress(scope, "人气榜", detail=f"共 {len(rows)} 只")
        return [dict(r) for r in rows if isinstance(r, dict)]
    except Exception as e:
        log_tool_error("同花顺人气股 | ths.hot_stock", e)
        return []


async def fetch_concept_boards(context: str = "概念四榜"):
    from quant.data.sources.ths import concept_board_top_lists

    try:

        async def _call():
            return await concept_board_top_lists("即时")

        return await alimit("ths", _call)
    except Exception as exc:
        log_tool_error(context, exc)
        return None, None, None, None


async def fetch_industry_board(context: str, sort_key: str, desc: bool = True) -> list | None:
    from quant.data.sources.ths import hyylb

    try:

        async def _call():
            return await hyylb(sort_key, desc)

        return await alimit("ths", _call)
    except Exception:
        log_tool_error(f"{context} sort_key={sort_key!r} desc={desc}")
        return None


async def fetch_market_fund_flow(n: int) -> list | None:
    try:
        return await run_in_threadpool(fetch_market_fund_flow_last, n)
    except Exception as e:
        log_tool_error("大盘资金流 | ak.stock_market_fund_flow", e)
        return None


async def async_holding_rows() -> list:
    from quant.store.state import get_holdings

    return await run_in_threadpool(get_holdings)


def unpack_concept_boards(concept_boards) -> tuple:
    if concept_boards is None or concept_boards[0] is None:
        return None, None, None, None
    return concept_boards
