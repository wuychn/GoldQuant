"""DefaultInfoSource：个股基本信息走东财。"""

from __future__ import annotations

from typing import Any

from quant.data.sources.info.eastmoney import EastmoneyInfoSource


class DefaultInfoSource:
    name = "default"

    def __init__(self) -> None:
        self._em = EastmoneyInfoSource()

    def fetch_stock_info(self, symbol: str) -> dict[str, Any]:
        return self._em.fetch_stock_info(symbol)
