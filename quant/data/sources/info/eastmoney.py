"""EastmoneyInfoSource：东财系资讯/基本信息（个股基本信息 jbxx）。

只实现东财支持的接口；未实现抛 NotImplementedError（接口级换源，不隐式 fallback）。
供 ``data.sources.info: {fetch_stock_info: eastmoney}``。
"""

from __future__ import annotations

from typing import Any


class EastmoneyInfoSource:
    name = "eastmoney"

    def fetch_stock_info(self, symbol: str) -> dict[str, Any]:
        """个股基本信息（东财 ``jbxx``）。"""
        from quant.data.sources.eastmoney import jbxx

        return jbxx(symbol)

    def fetch_news(self) -> list[dict[str, Any]]:
        raise NotImplementedError("eastmoney 未实现 fetch_news（可用 akshare/default）")
