"""同花顺数据源：爬虫 + 聚合 API + 限流出口。"""

from __future__ import annotations

from quant.data.sources.rate_limit import alimit
from quant.data.sources.ths.concept import fetch_ths_concept_fund_flow
from quant.data.sources.ths.funds import ThsFundsFetchError, fetch_stock_funds_cached, reset_ths_funds_cache_for_tests
from quant.data.sources.ths.hexin import get_v
from quant.data.sources.ths.industry import fetch_ths_industry_names, fetch_ths_industry_summary

_API_EXPORTS = frozenset(
    {
        "call_ths_wencai",
        "concept_board_top_lists",
        "cxfl",
        "cxg",
        "ggzjl",
        "hot_stock",
        "hyylb",
        "ljqs",
        "lxsz",
        "wcxg",
        "zdfb_ths",
        "zdfb_v2_realtime",
    }
)


def __getattr__(name: str):
    if name in _API_EXPORTS:
        from . import api as _api

        return getattr(_api, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


async def fetch_stock_funds(symbol: str, **kwargs):
    return await alimit("ths", lambda: fetch_stock_funds_cached(symbol, **kwargs))


async def fetch_concept_fund_flow(symbol: str = "即时"):
    return await alimit("ths", lambda: fetch_ths_concept_fund_flow(symbol))


__all__ = [
    "ThsFundsFetchError",
    "call_ths_wencai",
    "concept_board_top_lists",
    "cxfl",
    "cxg",
    "fetch_concept_fund_flow",
    "fetch_stock_funds",
    "fetch_stock_funds_cached",
    "fetch_ths_concept_fund_flow",
    "fetch_ths_industry_names",
    "fetch_ths_industry_summary",
    "get_v",
    "ggzjl",
    "hot_stock",
    "hyylb",
    "ljqs",
    "lxsz",
    "reset_ths_funds_cache_for_tests",
    "wcxg",
    "zdfb_ths",
    "zdfb_v2_realtime",
]
