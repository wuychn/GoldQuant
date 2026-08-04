"""AkshareInfoSource：akshare 系资讯（全局新闻）。

包装 ``AkshareNewsSource.fetch_global``；未实现 fetch_stock_info 抛 NotImplementedError。
供 ``data.sources.info: {fetch_news: akshare}``。
"""

from __future__ import annotations

from typing import Any


class AkshareInfoSource:
    name = "akshare"

    def fetch_news(self) -> list[dict[str, Any]]:
        """全局新闻（东财 + 同花顺聚合）；fixture 模式读离线样本。"""
        from common.config import get_settings

        if get_settings().QUANT_USE_LOCAL_FIXTURE:
            from quant.data.sources.fixture.news import FixtureNewsSource

            return FixtureNewsSource().fetch_global()
        from quant.data.sources.akshare.news import AkshareNewsSource

        return AkshareNewsSource().fetch_global()

    def fetch_stock_info(self, symbol: str) -> dict[str, Any]:
        raise NotImplementedError("akshare 未实现 fetch_stock_info（可用 eastmoney/default）")
