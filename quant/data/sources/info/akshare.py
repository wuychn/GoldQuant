"""AkshareInfoSource：未实现个股基本信息。"""

from __future__ import annotations

from typing import Any


class AkshareInfoSource:
    name = "akshare"

    def fetch_stock_info(self, symbol: str) -> dict[str, Any]:
        raise NotImplementedError("akshare 未实现 fetch_stock_info（可用 eastmoney/default）")
