"""DefaultInfoSource：组合源（每个方法委托到最优的单源实现）。

接口级：facade 一个方法 = 一个接口 = 一个源。
- fetch_news：akshare（AkshareInfoSource，东财+同花顺聚合）
- fetch_stock_info：东财（EastmoneyInfoSource，jbxx）
"""

from __future__ import annotations

from typing import Any

from quant.data.sources.info.akshare import AkshareInfoSource
from quant.data.sources.info.eastmoney import EastmoneyInfoSource


class DefaultInfoSource:
    name = "default"

    def __init__(self) -> None:
        self._ak = AkshareInfoSource()
        self._em = EastmoneyInfoSource()

    def fetch_news(self) -> list[dict[str, Any]]:
        return self._ak.fetch_news()

    def fetch_stock_info(self, symbol: str) -> dict[str, Any]:
        return self._em.fetch_stock_info(symbol)
