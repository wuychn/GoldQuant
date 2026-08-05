"""EastmoneyMarketSource：东财系运维行情（涨停池/涨跌分布/行业映射）。

只实现东财支持的接口；未实现抛 NotImplementedError（接口级换源，不隐式 fallback）。
供 ``data.sources.market: {fetch_ztgk_pool: eastmoney, fetch_ztgk_prev: eastmoney, ...}``。
"""

from __future__ import annotations

from typing import Any

from quant.data.sources.rate_limit import with_limit


class EastmoneyMarketSource:
    name = "eastmoney"

    def fetch_ztgk_pool(self) -> Any:
        from quant.data.sources.eastmoney import ztgc

        return with_limit("eastmoney", lambda: ztgc())

    def fetch_ztgk_prev(self, date: str) -> Any:
        from quant.data.sources.eastmoney import ztgc_with_date

        return with_limit("eastmoney", lambda: ztgc_with_date(date))

    def fetch_em_industry_map(self) -> Any:
        from quant.data.sources.eastmoney.industry import fetch_em_industry_board

        return fetch_em_industry_board()

    def fetch_industry_map(self) -> Any:
        return self.fetch_em_industry_map()

    def fetch_stop_resume(self, date: str) -> Any:
        """当日停复牌信息（东财 ``stock_tfp_em``）→ {代码: {停牌时间, 停牌截止, 停牌原因}}。"""
        import akshare as ak

        from quant.data.sources.rate_limit import with_limit

        df = with_limit("eastmoney", lambda: ak.stock_tfp_em(date=date.replace("-", "")))
        out: dict[str, dict] = {}
        if df is None or df.empty or "代码" not in df.columns:
            return out
        for _, r in df.iterrows():
            code = str(r.get("代码", "")).strip()
            if not code:
                continue
            out[code] = {
                "停牌时间": r.get("停牌时间"),
                "停牌截止时间": r.get("停牌截止时间"),
                "停牌原因": r.get("停牌原因"),
                "停牌期限": r.get("停牌期限"),
            }
        return out

    # ---- 未实现接口：显式报错 ----
    def fetch_index_spot(self) -> Any:
        raise NotImplementedError("eastmoney 未实现 fetch_index_spot（可用 akshare/default）")

    async def fetch_zqxy(self, *, market_phase: str = "intraday") -> Any:
        raise NotImplementedError("eastmoney 未实现 fetch_zqxy（可用 ths/default）")

    async def fetch_hot_raw(self, limit: int) -> Any:
        raise NotImplementedError("eastmoney 未实现 fetch_hot_raw（可用 ths/default）")

    async def fetch_concept_boards(self) -> Any:
        raise NotImplementedError("eastmoney 未实现 fetch_concept_boards（可用 ths/default）")

    async def fetch_industry_board(self, context: str, sort_key: str, desc: bool = True) -> Any:
        raise NotImplementedError("eastmoney 未实现 fetch_industry_board（可用 ths/default）")

    async def fetch_market_fund_flow(self, n: int) -> Any:
        raise NotImplementedError("eastmoney 未实现 fetch_market_fund_flow（可用 akshare/default）")

    async def fetch_ths_rank_screen(self, kind: str, *, symbol: str | None = None) -> Any:
        raise NotImplementedError("eastmoney 未实现 fetch_ths_rank_screen（可用 ths/default）")

    def fetch_em_hot_rank(self) -> Any:
        raise NotImplementedError("eastmoney 未实现 fetch_em_hot_rank（可用 akshare/default）")

    def fetch_em_concept_boards(self) -> Any:
        raise NotImplementedError("eastmoney 未实现 fetch_em_concept_boards（可用 akshare/default）")
