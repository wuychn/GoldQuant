"""Akshare market fund flow source (rate-limited)."""

from __future__ import annotations

from typing import Any

from app.utils.dataframe import dataframe_to_records
from quant.data.sources.rate_limit import with_limit


class AkshareFundFlowSource:
    def fetch_market(self) -> list[dict[str, Any]]:
        import akshare as ak

        def _call():
            return ak.stock_market_fund_flow()

        raw = with_limit("akshare", _call)
        return dataframe_to_records(raw) or []


def fetch_market_fund_flow_last(n: int) -> list[dict[str, Any]]:
    if n <= 0:
        return []
    recs = AkshareFundFlowSource().fetch_market()
    if not recs:
        return []
    return recs[-n:] if len(recs) >= n else recs
