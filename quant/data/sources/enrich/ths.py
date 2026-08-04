"""ThsEnrichSource：同花顺系个股 enrich（资金流 + 概念粘合度）。

只实现同花顺支持的接口；未实现抛 NotImplementedError（接口级换源，不隐式 fallback）。
供 ``data.sources.enrich: {fetch_stock_fund_flow: ths, fetch_concept_fit_rank: ths}``。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from common.utils.common_util import list_to_dict_v2
from common.utils.error_log import log_caught_error
from quant.data.sources.ths import ThsFundsFetchError, ggzjl

logger = logging.getLogger(__name__)


class ThsEnrichSource:
    name = "ths"

    async def fetch_stock_fund_flow(self, symbol: str) -> dict | None:
        """个股资金流（同花顺 ``ggzjl`` + 万元归一化）；失败返回 ``None``。"""
        try:
            r = await ggzjl(symbol)
            flash_ = r["flash"]
            v_ = list_to_dict_v2(flash_, "name", "sr")
            v_["大单流出"] = f"{v_['大单流出']} 万元"
            v_["中单流出"] = f"{v_['中单流出']} 万元"
            v_["小单流出"] = f"{v_['小单流出']} 万元"
            v_["小单流入"] = f"{v_['小单流入']} 万元"
            v_["中单流入"] = f"{v_['中单流入']} 万元"
            v_["大单流入"] = f"{v_['大单流入']} 万元"
            v_["总流入"] = f"{r['title']['zlr']} 万元"
            v_["总流出"] = f"{r['title']['zlc']} 万元"
            v_["净额"] = f"{r['title']['je']} 万元"
            return v_
        except ThsFundsFetchError as exc:
            status = f" HTTP {exc.http_status}" if exc.http_status is not None else ""
            log_caught_error(logger, f"stock_enrich [个股资金流 symbol={symbol!r}{status}]", exc)
            return None
        except Exception as exc:  # noqa: BLE001
            log_caught_error(logger, f"stock_enrich [个股资金流 symbol={symbol!r}]", exc)
            return None

    async def fetch_concept_fit_rank(self, symbol: str) -> list[dict[str, Any]]:
        """同花顺 F10 概念粘合度原始拉取（无缓存）。

        不做错误兜底——由 ``services/enrich.py`` 的 cache wrapper 统一 try/except + 记日志。
        """
        from quant.data.sources.ths.concept_fit_rank import get_concept_fit_rank_list

        return await asyncio.to_thread(get_concept_fit_rank_list, str(symbol).strip())

    # ---- 未实现接口：显式报错 ----
    async def fetch_stock_quote(self, symbol: str) -> dict | None:
        raise NotImplementedError("ths 未实现 fetch_stock_quote（可用 eastmoney/default）")

    async def fetch_stock_fund_flow_daily(self, symbol: str, *, days: int = 10) -> list[dict] | None:
        raise NotImplementedError("ths 未实现 fetch_stock_fund_flow_daily（可用 eastmoney/default）")

    async def fetch_stock_concepts(self, symbol: str, *, name: str | None = None) -> list[str]:
        raise NotImplementedError("ths 未实现 fetch_stock_concepts（可用 wencai/default）")

    async def fetch_stock_minute(self, symbol: str, *, context: str = "") -> list[dict[str, Any]] | None:
        raise NotImplementedError("ths 未实现 fetch_stock_minute（可用 akshare/default）")
