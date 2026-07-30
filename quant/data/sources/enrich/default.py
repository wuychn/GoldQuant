"""DefaultEnrichSource：个股 enrich 取数（盘口/资金流/概念/分钟K）。

从 ``services/enrich.py`` 搬出的"直接调源取数"部分：

- ``fetch_stock_quote``：盘口（东财 ``stock_bid_ask_em``），原 ``pk``。
- ``fetch_stock_fund_flow``：个股资金流（同花顺 ``ggzjl`` + 万元归一化 +
  ``ThsFundsFetchError`` 兜底），原 ``_ggzjl`` 整体。
- ``fetch_stock_fund_flow_daily``：资金流日线（东财 ``zj``），原 ``_fund_flow_daily``。
- ``fetch_concept_fit_rank``：同花顺 F10 概念粘合度原始拉取（无缓存），原
  ``fetch_stock_concept_fit_ths`` 内的取数行。
- ``fetch_stock_concepts``：问财所属概念原始拉取（无缓存），原 ``fetch_stock_concepts_wcxg``
  内的取数行。
- ``fetch_stock_minute``：当日 1 分钟 K（akshare ``stock_zh_a_hist_pre_min_em``），原
  ``services/market_enrich.stock_intraday_minute_zh``（搬到 sources 以满足分层：
  sources 不能 import services）。

归一化/单点错误兜底随取数搬入；concept 三级 cache fallback、io_tasks 并发、
jbxx/concept cache、archive 读写仍在 ``services/enrich.py``。

换源 = 配置选 ``data.sources.enrich``；下游 ``services/enrich.py`` 走 facade 零感知。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from common.utils.common_util import list_to_dict_v2
from common.utils.dataframe import dataframe_to_records
from common.utils.error_log import log_caught_error
from quant.data.sources.eastmoney import pk, zj
from quant.data.sources.ths import ThsFundsFetchError, ggzjl, wcxg

logger = logging.getLogger(__name__)


def _log_error(context: str, exc: Exception | None = None) -> None:
    log_caught_error(logger, f"stock_enrich [{context}]", exc)


class DefaultEnrichSource:
    name = "default"

    async def fetch_stock_quote(self, symbol: str) -> dict | None:
        """盘口（东财 ``stock_bid_ask_em``）；失败返回 ``None``。"""

        def _call() -> dict | None:
            try:
                return pk(symbol)
            except Exception as exc:  # noqa: BLE001
                _log_error(f"盘口 symbol={symbol!r}", exc)
                return None

        return await asyncio.to_thread(_call)

    async def fetch_stock_fund_flow(self, symbol: str) -> dict | None:
        """个股资金流（同花顺 ``ggzjl`` + 万元归一化）；失败返回 ``None``。

        ``ThsFundsFetchError`` 单独记 HTTP 状态；其余异常统一兜底。
        """
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
            _log_error(f"个股资金流 symbol={symbol!r}", exc)
            return None

    async def fetch_stock_fund_flow_daily(self, symbol: str, *, days: int = 10) -> list[dict] | None:
        """个股资金流日线（东财 ``zj``）；返回最近 ``days`` 条；失败/空返回 ``None``。"""
        try:
            rows = await asyncio.to_thread(zj, symbol)
            if not isinstance(rows, list) or not rows:
                return None
            return rows[-days:]
        except Exception as exc:  # noqa: BLE001
            _log_error(f"个股资金流日线 symbol={symbol!r}", exc)
            return None

    async def fetch_concept_fit_rank(self, symbol: str) -> list[dict[str, Any]]:
        """同花顺 F10 概念粘合度原始拉取（无缓存）。

        不做错误兜底——由 ``services/enrich.py`` 的 cache wrapper 统一 try/except + 记日志。
        空列表表示无数据。
        """
        from quant.data.sources.ths.concept_fit_rank import get_concept_fit_rank_list

        return await asyncio.to_thread(get_concept_fit_rank_list, str(symbol).strip())

    async def fetch_stock_concepts(self, symbol: str, *, name: str | None = None) -> list[str]:
        """问财所属概念原始拉取（无缓存）。

        不做错误兜底——由 ``services/enrich.py`` 的 cache wrapper 统一 try/except + 记日志。
        空列表表示无数据。
        """
        question = str(symbol).strip()
        if name:
            question = f"{question} {str(name).strip()}"
        return await wcxg(question)

    async def fetch_stock_minute(self, symbol: str, *, context: str = "") -> list[dict[str, Any]] | None:
        """东财当日 1 分钟 K（09:15–15:00，含集合竞价与连续竞价）；失败返回 ``None``。

        akshare ``stock_zh_a_hist_pre_min_em``（原 ``services/market_enrich.stock_intraday_minute_zh``）。
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
