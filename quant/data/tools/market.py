"""Market data tools facade：委托 MarketSource 取数 + 业务编排（过滤/高度/prefilter/解包）。

取数（含多源 fallback）在 ``quant/data/sources/market/``；本模块只做标的池过滤、涨停高度、
人气榜 prefilter、概念板解包等业务编排。调用方（payload 等）零感知。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi.concurrency import run_in_threadpool

from quant.data.calendar import prev_trading_day
from quant.data.tools.payload_utils import zt_height
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


def _market_source():
    from quant.data.sources.factory import get_market_source

    return get_market_source()


async def fetch_index_spot() -> list | None:
    try:
        from quant.data.sources.interface import try_with_fallback_async

        return await try_with_fallback_async("market", "fetch_index_spot")
    except Exception as e:
        log_tool_error("大盘指数", e)
        return None


async def fetch_zqxy(*, market_phase: str = "intraday") -> Any:
    try:
        from quant.data.sources.interface import try_with_fallback_async

        return await try_with_fallback_async("market", "fetch_zqxy", market_phase=market_phase)
    except Exception:
        log_tool_error("赚钱效应")
        return None


def _apply_row_limit(rows: list | None, limit: int | None) -> list:
    if not isinstance(rows, list):
        return []
    allowed = apply_symbol_pool_filter(rows)
    if limit is None:
        return allowed
    return allowed[:limit]


def ztgk_rows(settings: Any, zt_full: list, *, more: bool = False, zrzt: list | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    row_limit = settings.quant_bulk_row_limit()
    zt_allowed = apply_symbol_pool_filter(zt_full)
    height = zt_height(zt_allowed)
    result["今日涨停"] = zt_allowed[:row_limit] if row_limit is not None else zt_allowed
    result["市场高度"] = f"{height}连板"
    if more and zrzt is not None:
        result["昨日涨停"] = _apply_row_limit(zrzt, row_limit)
    return result


async def fetch_ztgk(settings: Any, more: bool = False, *, zt_full: list | None = None):
    from quant.data.sources.interface import try_with_fallback_async

    try:
        pool = zt_full if zt_full is not None else await try_with_fallback_async("market", "fetch_ztgk_pool")
        zrzt: list | None = None
        if more:
            try:
                prev = prev_trading_day(cn_now().date())
                if prev is None:
                    raise ValueError("无上一交易日")
                zrzt = await try_with_fallback_async(
                    "market", "fetch_ztgk_prev", date=prev.strftime("%Y%m%d")
                )
            except Exception:
                log_tool_error("昨日涨停股池全量")
        return ztgk_rows(settings, pool if isinstance(pool, list) else [], more=more, zrzt=zrzt)
    except Exception as e:
        log_tool_error("今日涨停股全量", e)
        return {}


async def fetch_hot(settings: Any, *, progress_scope: str | None = "during_market") -> list:
    try:
        from quant.data.sources.interface import try_with_fallback_async

        n = settings.quant_hot_list_limit()
        raw_hot = await try_with_fallback_async("market", "fetch_hot_raw", limit=n)
        rows = prefilter_popularity(raw_hot if isinstance(raw_hot, list) else [])
        scope = progress_scope or "during_market"
        log_progress(scope, "人气榜", detail=f"共 {len(rows)} 只")
        return [dict(r) for r in rows if isinstance(r, dict)]
    except Exception as e:
        log_tool_error("同花顺人气股", e)
        return []


async def fetch_concept_boards(context: str = "概念四榜"):
    try:
        from quant.data.sources.interface import try_with_fallback_async

        return await try_with_fallback_async("market", "fetch_concept_boards")
    except Exception as exc:
        log_tool_error(context, exc)
        return None, None, None, None


async def fetch_industry_board(context: str, sort_key: str, desc: bool = True) -> list | None:
    try:
        from quant.data.sources.interface import try_with_fallback_async

        return await try_with_fallback_async(
            "market", "fetch_industry_board", context=context, sort_key=sort_key, desc=desc
        )
    except Exception:
        log_tool_error(f"{context} sort_key={sort_key!r} desc={desc}")
        return None


async def fetch_market_fund_flow(n: int) -> list | None:
    try:
        from quant.data.sources.interface import try_with_fallback_async

        return await try_with_fallback_async("market", "fetch_market_fund_flow", n=n)
    except Exception as e:
        log_tool_error("大盘资金流", e)
        return None


def fetch_stock_fund_flow_rank(
    *,
    indicator: str = "5日",
    as_of: str | None = None,
    page_size: int | None = None,
    page_interval: float | None = None,
    page_interval_min: float | None = None,
    page_interval_max: float | None = None,
    force: bool = False,
):
    """个股资金流排名（分页）→ DataFrame(code, main_net_inflow)。

    页间默认随机 [min,max]（yml ``fund_flow_rank_page_interval_*``，默认 10–30）。
    编排（分页失败→逐票）见 ``quant.data.fund_flow_5d.fetch_main_net_inflow_5d``。
    """
    from quant.config import load_quant_config
    from quant.data.sources.interface import try_with_fallback

    data = load_quant_config().get("data") or {}
    if page_size is None:
        page_size = int(data.get("fund_flow_rank_page_size", 100))
    if page_interval is not None:
        page_interval_min = page_interval_max = max(10.0, float(page_interval))
    if page_interval_min is None:
        page_interval_min = float(
            data.get("fund_flow_rank_page_interval_min", data.get("fund_flow_rank_page_interval", 10))
        )
    if page_interval_max is None:
        page_interval_max = float(data.get("fund_flow_rank_page_interval_max", 30))
    burst_lo = int(data.get("fund_flow_rank_burst_pages_min", 2))
    burst_hi = int(data.get("fund_flow_rank_burst_pages_max", 4))
    pause_lo = float(
        data.get(
            "fund_flow_rank_batch_pause_min_sec",
            data.get("fund_flow_rank_fail_cooldown_sec", 120),
        )
    )
    pause_hi = float(data.get("fund_flow_rank_batch_pause_max_sec", 240))
    return try_with_fallback(
        "market",
        "fetch_stock_fund_flow_rank",
        indicator=indicator,
        page_size=max(1, int(page_size)),
        page_interval_min=max(10.0, float(page_interval_min)),
        page_interval_max=max(10.0, float(page_interval_max)),
        burst_pages_min=max(1, burst_lo),
        burst_pages_max=max(1, burst_hi),
        batch_pause_min_sec=max(0.0, pause_lo),
        batch_pause_max_sec=max(0.0, pause_hi),
        as_of=as_of,
        force=force,
    )


async def async_holding_rows() -> list:
    from quant.store.state import get_holdings

    return await run_in_threadpool(get_holdings)


def unpack_concept_boards(concept_boards) -> tuple:
    if concept_boards is None or concept_boards[0] is None:
        return None, None, None, None
    return concept_boards
