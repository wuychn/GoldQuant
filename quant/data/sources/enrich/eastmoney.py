"""EastmoneyEnrichSource：东财系个股 enrich（盘口 + 资金流日线）。

只实现东财支持的接口；未实现接口抛 NotImplementedError（接口级换源，不隐式 fallback）。
供 ``data.sources.enrich: {fetch_stock_quote: eastmoney, fetch_stock_fund_flow_daily: eastmoney}``。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from common.utils.error_log import log_caught_error
from quant.data.sources.eastmoney import pk, zj

logger = logging.getLogger(__name__)


class EastmoneyEnrichSource:
    name = "eastmoney"

    async def fetch_stock_quote(self, symbol: str) -> dict | None:
        """盘口（东财 ``stock_bid_ask_em``）；失败返回 ``None``。"""

        def _call() -> dict | None:
            try:
                return pk(symbol)
            except Exception as exc:  # noqa: BLE001
                log_caught_error(logger, f"stock_enrich [盘口 symbol={symbol!r}]", exc)
                return None

        return await asyncio.to_thread(_call)

    async def fetch_stock_fund_flow_daily(self, symbol: str, *, days: int = 10) -> list[dict] | None:
        """个股资金流日线（东财 ``zj``）；返回最近 ``days`` 条；失败/空返回 ``None``。"""
        try:
            rows = await asyncio.to_thread(zj, symbol)
            if not isinstance(rows, list) or not rows:
                return None
            return rows[-days:]
        except Exception as exc:  # noqa: BLE001
            log_caught_error(logger, f"stock_enrich [资金流日线 symbol={symbol!r}]", exc)
            return None

    # ---- 未实现接口：显式报错 ----
    async def fetch_stock_fund_flow(self, symbol: str) -> dict | None:
        raise NotImplementedError("eastmoney 未实现 fetch_stock_fund_flow（可用 ths/default）")

    async def fetch_concept_fit_rank(self, symbol: str) -> list[dict[str, Any]]:
        raise NotImplementedError("eastmoney 未实现 fetch_concept_fit_rank（可用 ths/default）")

    async def fetch_stock_concepts(self, symbol: str, *, name: str | None = None) -> list[str]:
        raise NotImplementedError("eastmoney 未实现 fetch_stock_concepts（可用 wencai/default）")

    async def fetch_stock_minute(self, symbol: str, *, context: str = "") -> list[dict[str, Any]] | None:
        raise NotImplementedError("eastmoney 未实现 fetch_stock_minute（可用 akshare/default）")
