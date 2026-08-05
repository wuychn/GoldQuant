"""ThsMarketSource：同花顺系运维行情（赚钱效应/热度/概念/行业/形态榜）。

只实现同花顺支持的接口；未实现抛 NotImplementedError（接口级换源，不隐式 fallback）。
供 ``data.sources.market: {fetch_zqxy: ths, fetch_hot_raw: ths, ...}``。
"""

from __future__ import annotations

from typing import Any

from quant.data.sources.rate_limit import alimit


class ThsMarketSource:
    name = "ths"

    async def fetch_zqxy(self, *, market_phase: str = "intraday") -> Any:
        from quant.data.sources.etf52.zdfb import zdfb_52etf
        from quant.data.sources.ths import zdfb_ths, zdfb_v2_realtime

        for coro_factory, label in (
            (lambda: alimit("ths", lambda: zdfb_v2_realtime()), "赚钱效应 | 同花顺V2接口"),
            (lambda: zdfb_52etf(market_phase=market_phase), "赚钱效应 | 52etf涨跌分布"),
            (lambda: alimit("ths", lambda: zdfb_ths()), "赚钱效应 | 同花顺涨跌分布"),
        ):
            try:
                return await coro_factory()
            except Exception:
                import logging

                logging.getLogger(__name__).warning("量化数据 [%s]", label)
        return None

    async def fetch_hot_raw(self, limit: int) -> Any:
        from quant.data.sources.ths import hot_stock

        return await alimit("ths", lambda: hot_stock(limit))

    async def fetch_concept_boards(self) -> Any:
        """同花顺概念板块 → 统一返回 DataFrame(板块名称, 涨跌幅)。

        原始返回 tuple(4×list[dict])，含 `行业`/`行业-涨跌幅` 字段；此处合并四子列表
        并归一到业务层统一 schema。
        """
        from quant.data.sources.ths import concept_board_top_lists

        import pandas as pd

        result = await alimit("ths", lambda: concept_board_top_lists("即时"))
        if not isinstance(result, tuple):
            return pd.DataFrame(columns=["板块名称", "涨跌幅"])
        rows: list[dict] = []
        for sub in result:
            if isinstance(sub, list):
                for r in sub:
                    if isinstance(r, dict):
                        name = str(r.get("行业") or r.get("板块") or "").strip()
                        pct = r.get("行业-涨跌幅")
                        if name and pct is not None:
                            rows.append({"板块名称": name, "涨跌幅": pct})
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["板块名称", "涨跌幅"])

    async def fetch_industry_board(self, context: str, sort_key: str, desc: bool = True) -> Any:
        from quant.data.sources.ths import hyylb

        return await alimit("ths", lambda: hyylb(sort_key, desc))

    async def fetch_ths_rank_screen(self, kind: str, *, symbol: str | None = None) -> Any:
        from quant.data.sources.ths import cxfl, cxg, ljqs, lxsz

        if kind == "cxg":
            return await alimit("ths", lambda: cxg(symbol or "创月新高"))
        fn = {"lxsz": lxsz, "cxfl": cxfl, "ljqs": ljqs}.get(kind)
        if fn is None:
            raise ValueError(f"未知 ths 形态榜 kind={kind}")
        return await alimit("ths", lambda: fn())

    # ---- 未实现接口：显式报错 ----
    def fetch_index_spot(self) -> Any:
        raise NotImplementedError("ths 未实现 fetch_index_spot（可用 akshare/default）")

    def fetch_ztgk_pool(self) -> Any:
        raise NotImplementedError("ths 未实现 fetch_ztgk_pool（可用 eastmoney/default）")

    def fetch_ztgk_prev(self, date: str) -> Any:
        raise NotImplementedError("ths 未实现 fetch_ztgk_prev（可用 eastmoney/default）")

    async def fetch_market_fund_flow(self, n: int) -> Any:
        raise NotImplementedError("ths 未实现 fetch_market_fund_flow（可用 akshare/default）")

    def fetch_em_industry_map(self) -> Any:
        raise NotImplementedError("ths 未实现 fetch_em_industry_map（可用 eastmoney/default）")

    def fetch_industry_map(self) -> Any:
        raise NotImplementedError("ths 未实现 fetch_industry_map（可用 eastmoney/sina/default）")

    def fetch_em_hot_rank(self) -> Any:
        raise NotImplementedError("ths 未实现 fetch_em_hot_rank（可用 akshare/default）")

    def fetch_em_concept_boards(self) -> Any:
        raise NotImplementedError("ths 未实现 fetch_em_concept_boards（可用 akshare/default）")

    def fetch_stop_resume(self, date: str) -> Any:
        raise NotImplementedError("ths 未实现 fetch_stop_resume（可用 eastmoney/default）")
