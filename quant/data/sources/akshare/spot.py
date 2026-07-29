"""Akshare spot market source (rate-limited)."""

from __future__ import annotations

from typing import Any

from quant.data.sources.rate_limit import with_limit


class AkshareSpotSource:
    def fetch_all(self) -> list[dict[str, Any]]:
        import akshare as ak

        from app.utils.dataframe import dataframe_to_records
        from quant.data.fetch import _normalize_spot

        def _call():
            df = ak.stock_zh_a_spot_em()
            return _normalize_spot(df)

        df = with_limit("akshare", _call)
        if df is None or df.empty:
            return []
        return df.to_dict("records")
