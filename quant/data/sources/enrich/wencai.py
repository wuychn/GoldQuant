"""WencaiEnrichSource：问财系个股 enrich（所属概念）。

只实现问财支持的接口；未实现抛 NotImplementedError（接口级换源，不隐式 fallback）。
供 ``data.sources.enrich: {fetch_stock_concepts: wencai}``。
"""

from __future__ import annotations

from typing import Any

from quant.data.sources.ths import wcxg


class WencaiEnrichSource:
    name = "wencai"

    async def fetch_stock_concepts(self, symbol: str, *, name: str | None = None) -> list[str]:
        """问财所属概念原始拉取（无缓存）。

        不做错误兜底——由 ``services/enrich.py`` 的 cache wrapper 统一 try/except + 记日志。
        """
        question = str(symbol).strip()
        if name:
            question = f"{question} {str(name).strip()}"
        return await wcxg(question)

    # ---- 未实现接口：显式报错 ----
    async def fetch_stock_quote(self, symbol: str) -> dict | None:
        raise NotImplementedError("wencai 未实现 fetch_stock_quote（可用 eastmoney/default）")

    async def fetch_stock_fund_flow(self, symbol: str) -> dict | None:
        raise NotImplementedError("wencai 未实现 fetch_stock_fund_flow（可用 ths/default）")

    async def fetch_stock_fund_flow_daily(self, symbol: str, *, days: int = 10) -> list[dict] | None:
        raise NotImplementedError("wencai 未实现 fetch_stock_fund_flow_daily（可用 eastmoney/default）")

    async def fetch_concept_fit_rank(self, symbol: str) -> list[dict[str, Any]]:
        raise NotImplementedError("wencai 未实现 fetch_concept_fit_rank（可用 ths/default）")

    async def fetch_stock_minute(self, symbol: str, *, context: str = "") -> list[dict[str, Any]] | None:
        raise NotImplementedError("wencai 未实现 fetch_stock_minute（可用 akshare/default）")
