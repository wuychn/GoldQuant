"""Akshare index spot source (rate-limited)."""

from __future__ import annotations

from typing import Any

from common.utils.dataframe import dataframe_to_records
from quant.data.sources.rate_limit import with_limit

_INDEX_SERIAL_WHITELIST = (1, 2, 4)


class AkshareIndexSource:
    def fetch_hs_important(self) -> list[dict[str, Any]] | None:
        import akshare as ak

        def _call():
            return ak.stock_zh_index_spot_em(symbol="沪深重要指数")

        try:
            raw = with_limit("akshare", _call)
            recs = dataframe_to_records(raw)
            return [item for item in recs if item.get("序号") in _INDEX_SERIAL_WHITELIST]
        except Exception:
            return None
