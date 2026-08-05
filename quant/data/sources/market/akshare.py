"""AkshareMarketSource：akshare 系运维行情（指数 spot/市场资金流/东财人气·概念板）。

只实现 akshare 支持的接口；未实现抛 NotImplementedError（接口级换源，不隐式 fallback）。
供 ``data.sources.market: {fetch_index_spot: akshare, fetch_market_fund_flow: akshare, ...}``。
"""

from __future__ import annotations

from typing import Any

from fastapi.concurrency import run_in_threadpool

from quant.data.sources.rate_limit import with_limit


class AkshareMarketSource:
    name = "akshare"

    def fetch_index_spot(self) -> Any:
        from common.config import get_settings

        if get_settings().QUANT_USE_LOCAL_FIXTURE:
            from quant.data.sources.fixture.index import FixtureIndexSource

            return FixtureIndexSource().fetch_hs_important()
        from quant.data.sources.akshare.index import AkshareIndexSource

        return AkshareIndexSource().fetch_hs_important()

    async def fetch_market_fund_flow(self, n: int) -> Any:
        from quant.data.sources.akshare.fund_flow import fetch_market_fund_flow_last

        return await run_in_threadpool(fetch_market_fund_flow_last, n)

    def fetch_em_hot_rank(self) -> Any:
        """东财人气榜 → 统一 DataFrame(code: str, rank: float)。

        原始返回中文列名（代码/当前排名），此处归一化；业务层直接读 code/rank。
        """
        import akshare as ak
        import pandas as pd

        from quant.data.sources.rate_limit import with_limit

        df = with_limit("akshare", lambda: ak.stock_hot_rank_em())
        if df is None or df.empty:
            return pd.DataFrame(columns=["code", "rank"])
        # 归一列名
        code_col = "代码" if "代码" in df.columns else df.columns[1] if len(df.columns) > 1 else df.columns[0]
        rank_col = "当前排名" if "当前排名" in df.columns else None
        out = pd.DataFrame()
        out["code"] = df[code_col].astype(str).str.strip() if code_col in df.columns else ""
        out["rank"] = df[rank_col] if rank_col else range(1, len(df) + 1)
        out["rank"] = pd.to_numeric(out["rank"], errors="coerce")
        return out[["code", "rank"]]

    def fetch_em_concept_boards(self) -> Any:
        """东财概念板块 → 统一 DataFrame(板块名称, 涨跌幅)。"""
        import akshare as ak
        import pandas as pd

        from quant.data.sources.rate_limit import with_limit

        df = with_limit("akshare", lambda: ak.stock_board_concept_name_em())
        if df is None or df.empty:
            return pd.DataFrame(columns=["板块名称", "涨跌幅"])
        name_col = "板块名称" if "板块名称" in df.columns else df.columns[1]
        pct_col = next((c for c in df.columns if "涨跌幅" in str(c)), None)
        if not pct_col:
            return pd.DataFrame(columns=["板块名称", "涨跌幅"])
        return df[[name_col, pct_col]].rename(columns={name_col: "板块名称", pct_col: "涨跌幅"}).reset_index(drop=True)

    async def fetch_concept_boards(self) -> Any:
        """东财概念板块 → 统一返回 DataFrame(板块名称, 涨跌幅)。

        与 ``fetch_em_concept_boards`` 同实现；统一接口名，业务层用
        ``try_with_fallback("market", "fetch_concept_boards")`` 即可。
        """
        from fastapi.concurrency import run_in_threadpool

        return await run_in_threadpool(self.fetch_em_concept_boards)

    # ---- 未实现接口：显式报错 ----
    async def fetch_zqxy(self, *, market_phase: str = "intraday") -> Any:
        raise NotImplementedError("akshare 未实现 fetch_zqxy（可用 ths/default）")

    def fetch_ztgk_pool(self) -> Any:
        raise NotImplementedError("akshare 未实现 fetch_ztgk_pool（可用 eastmoney/default）")

    def fetch_ztgk_prev(self, date: str) -> Any:
        raise NotImplementedError("akshare 未实现 fetch_ztgk_prev（可用 eastmoney/default）")

    async def fetch_hot_raw(self, limit: int) -> Any:
        raise NotImplementedError("akshare 未实现 fetch_hot_raw（可用 ths/default）")

    async def fetch_industry_board(self, context: str, sort_key: str, desc: bool = True) -> Any:
        raise NotImplementedError("akshare 未实现 fetch_industry_board（可用 ths/default）")

    async def fetch_ths_rank_screen(self, kind: str, *, symbol: str | None = None) -> Any:
        raise NotImplementedError("akshare 未实现 fetch_ths_rank_screen（可用 ths/default）")

    def fetch_em_industry_map(self) -> Any:
        raise NotImplementedError("akshare 未实现 fetch_em_industry_map（可用 eastmoney/default）")

    def fetch_industry_map(self) -> Any:
        raise NotImplementedError("akshare 未实现 fetch_industry_map（可用 eastmoney/sina/default）")

    def fetch_stop_resume(self, date: str) -> Any:
        raise NotImplementedError("akshare 未实现 fetch_stop_resume（可用 eastmoney/default）")
