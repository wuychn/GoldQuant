"""DefaultInfoSource：资讯/基本信息取数（全局新闻 + 个股基本信息）。

- ``fetch_news``：全局新闻（东财+同花顺聚合）。fixture 模式读 ``news_global.json``
  离线样本（原 ``FixtureNewsSource``），否则走 ``AkshareNewsSource.fetch_global``。
- ``fetch_stock_info``：个股基本信息（东财 ``jbxx`` = ``_fetch_stock_individual_info_em``），
  供 ``jbxx_cache`` 的 live 取数委托。

换源 = 配置选 ``data.sources.info``；下游（``payload.build_news_payload`` /
``jbxx_cache``）走 facade 零感知。
"""

from __future__ import annotations

from typing import Any


def _fixture_mode() -> bool:
    from common.config import get_settings

    return bool(get_settings().QUANT_USE_LOCAL_FIXTURE)


class DefaultInfoSource:
    name = "default"

    def fetch_news(self) -> list[dict[str, Any]]:
        """全局新闻（东财 + 同花顺聚合）；fixture 模式读离线样本。"""
        if _fixture_mode():
            from quant.data.sources.fixture.news import FixtureNewsSource

            return FixtureNewsSource().fetch_global()
        from quant.data.sources.akshare.news import AkshareNewsSource

        return AkshareNewsSource().fetch_global()

    def fetch_stock_info(self, symbol: str) -> dict[str, Any]:
        """个股基本信息（东财 ``jbxx``）。"""
        from quant.data.sources.eastmoney import jbxx

        return jbxx(symbol)
