"""SinaMarketSource：新浪系运维行情（行业映射）。

目前新浪支持行业映射（``stock_classify_sina('申万行业')`` → code→行业）；
其余接口抛 NotImplementedError（接口级换源，不隐式 fallback）。

供 ``data.sources.market: {fetch_industry_map: sina}``。
"""

from __future__ import annotations

from typing import Any


class SinaMarketSource:
    name = "sina"

    def fetch_industry_map(self) -> Any:
        """新浪申万行业分类 → {代码: 行业}（全 A 按行业枚举）。"""
        import akshare as ak

        df = ak.stock_classify_sina(symbol="申万行业")
        out: dict[str, str] = {}
        if df is None or df.empty or "class" not in df.columns:
            return out
        for _, r in df.iterrows():
            code = str(r.get("code", "")).strip()
            ind = str(r.get("class", "")).strip()
            if code and ind:
                out[code] = ind
        return out

    # ---- 未实现接口：显式报错 ----
    def fetch_index_spot(self) -> Any:
        raise NotImplementedError("sina 未实现 fetch_index_spot（可用 akshare/default）")

    async def fetch_zqxy(self, *, market_phase: str = "intraday") -> Any:
        raise NotImplementedError("sina 未实现 fetch_zqxy（可用 ths/default）")

    def fetch_ztgk_pool(self) -> Any:
        raise NotImplementedError("sina 未实现 fetch_ztgk_pool（可用 eastmoney/default）")

    def fetch_ztgk_prev(self, date: str) -> Any:
        raise NotImplementedError("sina 未实现 fetch_ztgk_prev（可用 eastmoney/default）")

    async def fetch_hot_raw(self, limit: int) -> Any:
        raise NotImplementedError("sina 未实现 fetch_hot_raw（可用 ths/default）")

    async def fetch_concept_boards(self) -> Any:
        raise NotImplementedError("sina 未实现 fetch_concept_boards（可用 ths/default）")

    async def fetch_industry_board(self, context: str, sort_key: str, desc: bool = True) -> Any:
        raise NotImplementedError("sina 未实现 fetch_industry_board（可用 ths/default）")

    async def fetch_market_fund_flow(self, n: int) -> Any:
        raise NotImplementedError("sina 未实现 fetch_market_fund_flow（可用 akshare/default）")

    async def fetch_ths_rank_screen(self, kind: str, *, symbol: str | None = None) -> Any:
        raise NotImplementedError("sina 未实现 fetch_ths_rank_screen（可用 ths/default）")

    def fetch_em_industry_map(self) -> Any:
        raise NotImplementedError("sina 未实现 fetch_em_industry_map（东财专属，可用 eastmoney/default）")

    def fetch_em_hot_rank(self) -> Any:
        raise NotImplementedError("sina 未实现 fetch_em_hot_rank（可用 akshare/default）")

    def fetch_em_concept_boards(self) -> Any:
        raise NotImplementedError("sina 未实现 fetch_em_concept_boards（可用 akshare/default）")

    def fetch_stop_resume(self, date: str) -> Any:
        raise NotImplementedError("sina 未实现 fetch_stop_resume（可用 eastmoney/default）")
