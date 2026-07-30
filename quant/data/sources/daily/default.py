"""DefaultDailySource：组合最优（推荐默认）。

不同接口实测最优源不同，故默认实现是组合：
- ``fetch_index``：东财 kline 直连（akshare ``index_zh_a_hist`` 走 clist 易被 TLS 反爬断）。
- 其余（hist/calendar/code_list/delisted/spot）：委托 ``AkshareDailySource``（akshare 走 kline/sina/stockapi，稳）。

换源 = 配置选 ``data.sources.daily``（akshare/eastmoney/未来 tushare）；下游只调 facade。
"""

from __future__ import annotations

import pandas as pd

from quant.data.sources.daily._shared import eastmoney_index_kline


class DefaultDailySource:
    name = "default"

    def __init__(self) -> None:
        from quant.data.sources.daily.akshare import AkshareDailySource

        self._ak = AkshareDailySource()

    def fetch_hist(self, code: str, *, start: str, end: str, adjust: str = "") -> pd.DataFrame:
        return self._ak.fetch_hist(code, start=start, end=end, adjust=adjust)

    def fetch_index(self, code: str, *, start: str, end: str) -> pd.DataFrame:
        return eastmoney_index_kline(code, start=start, end=end)

    def fetch_calendar(self) -> list[str]:
        return self._ak.fetch_calendar()

    def fetch_code_list(self) -> list[str]:
        return self._ak.fetch_code_list()

    def fetch_delisted_codes(self) -> pd.DataFrame:
        return self._ak.fetch_delisted_codes()

    def fetch_delisted_daily(self, code: str, *, start: str, end: str) -> pd.DataFrame:
        return self._ak.fetch_delisted_daily(code, start=start, end=end)

    def fetch_spot(self) -> pd.DataFrame:
        return self._ak.fetch_spot()
