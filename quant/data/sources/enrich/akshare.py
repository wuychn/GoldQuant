"""AkshareEnrichSource：akshare 系个股 enrich（当日分钟 K）。

只实现 akshare 支持的接口；未实现抛 NotImplementedError（接口级换源，不隐式 fallback）。
供 ``data.sources.enrich: {fetch_stock_minute: akshare}``。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from common.utils.dataframe import dataframe_to_records
from common.utils.error_log import log_caught_error

logger = logging.getLogger(__name__)


class AkshareEnrichSource:
    name = "akshare"

    async def fetch_stock_minute(self, symbol: str, *, context: str = "") -> list[dict[str, Any]] | None:
        """东财当日 1 分钟 K（09:15–15:00，含集合竞价与连续竞价）；失败返回 ``None``。

        akshare ``stock_zh_a_hist_pre_min_em``。
        """
        import akshare as ak

        def _call() -> list[dict[str, Any]] | None:
            try:
                df = ak.stock_zh_a_hist_pre_min_em(
                    symbol=str(symbol).strip(),
                    start_time="09:15:00",
                    end_time="15:00:00",
                )
                if df is None or df.empty:
                    return []
                return dataframe_to_records(df)
            except Exception as exc:  # noqa: BLE001
                log_caught_error(logger, f"stock_enrich [分钟行情 [{context}] symbol={symbol}]", exc)
                return None

        return await asyncio.to_thread(_call)

    # ---- 未实现接口：显式报错 ----
    async def fetch_stock_quote(self, symbol: str) -> dict | None:
        raise NotImplementedError("akshare 未实现 fetch_stock_quote（可用 eastmoney/default）")

    async def fetch_stock_fund_flow(self, symbol: str) -> dict | None:
        raise NotImplementedError("akshare 未实现 fetch_stock_fund_flow（可用 ths/default）")

    async def fetch_stock_fund_flow_daily(self, symbol: str, *, days: int = 10) -> list[dict] | None:
        """个股资金流日线历史（akshare ``stock_individual_fund_flow``）。

        东财 ``stock_individual_fund_flow`` 走 push2his（非 clist），与 ``zj`` 不同端点，
        作为东财 ``zj`` 断时的 fallback。返回与东财 ``zj`` 同结构的 list[dict]。
        """
        import akshare as ak

        def _call() -> list[dict] | None:
            try:
                code = str(symbol).strip()
                market = "sh" if code.startswith(("6", "9", "5")) else "sz" if code.startswith(("0", "2", "3")) else "bj"
                df = ak.stock_individual_fund_flow(stock=code, market=market)
                if df is None or df.empty:
                    return None
                recs = dataframe_to_records(df)
                return recs[-days:] if recs else None
            except Exception as exc:  # noqa: BLE001
                log_caught_error(logger, f"stock_enrich [资金流日线 akshare symbol={symbol}]", exc)
                return None

        return await asyncio.to_thread(_call)

    async def fetch_concept_fit_rank(self, symbol: str) -> list[dict[str, Any]]:
        raise NotImplementedError("akshare 未实现 fetch_concept_fit_rank（可用 ths/default）")

    async def fetch_stock_concepts(self, symbol: str, *, name: str | None = None) -> list[str]:
        raise NotImplementedError("akshare 未实现 fetch_stock_concepts（可用 wencai/default）")
