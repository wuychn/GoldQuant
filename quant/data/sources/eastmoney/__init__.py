"""东财数据源：dfcf 实现 + 限流出口。"""

from __future__ import annotations

import quant.data.sources.eastmoney.dfcf as _impl
from quant.data.sources.eastmoney.industry import fetch_em_industry_board
from quant.data.sources.rate_limit import with_limit

__all__ = [
    "fetch_em_industry_board",
    "hist",
    "hy",
    "jbxx",
    "pk",
    "ztgc",
    "ztgc_with_date",
    "zj",
    "pkyd",
    "hsgtzj",
    "cmfb",
    "_parse_stock_individual_info_payload",
]


def jbxx(symbol):
    return with_limit("eastmoney", lambda: _impl.jbxx(symbol))


def pk(symbol):
    return with_limit("eastmoney", lambda: _impl.pk(symbol))


def ztgc(filter_first: bool = False):
    return with_limit("eastmoney", lambda: _impl.ztgc(filter_first=filter_first))


def ztgc_with_date(trade_date):
    return with_limit("eastmoney", lambda: _impl.ztgc_with_date(trade_date))


def zj(symbol):
    return with_limit("eastmoney", lambda: _impl.zj(symbol))


def hist(symbol, period="daily", *, start_date=None, end_date=None):
    return with_limit(
        "eastmoney",
        lambda: _impl.hist(symbol, period, start_date=start_date, end_date=end_date),
    )


def pkyd(symbol):
    return with_limit("eastmoney", lambda: _impl.pkyd(symbol))


def hsgtzj():
    return with_limit("eastmoney", _impl.hsgtzj)


def cmfb(symbol):
    return with_limit("eastmoney", lambda: _impl.cmfb(symbol))


def hy():
    return fetch_em_industry_board()


def _parse_stock_individual_info_payload(data_json: dict) -> dict:
    return _impl._parse_stock_individual_info_payload(data_json)
