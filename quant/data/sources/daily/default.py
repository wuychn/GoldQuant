"""DefaultDailySource：组合最优（推荐默认），每个方法内部**单一源**。

组合原则：facade 一个方法 = 一个接口 = 一个源。default 是"每个接口各自选一个最稳的源"
的组合，但**单个方法内部不混源**（fetch_spot 只走新浪、fetch_hist 只走东财 kline 等）。
需要逐接口自定义时，用接口级配置（quant.yml ``data.sources.daily`` dict）。

- fetch_spot：新浪直连（东财 spot_em 走 clist 58 页 ~20 分钟易断）
- fetch_hist：东财 kline（akshare stock_zh_a_hist，push2his 稳）
- fetch_index：东财 kline 直连（eastmoney_index_kline，绕 akshare clist）
- fetch_calendar / code_list / delisted：akshare
"""

from __future__ import annotations

import pandas as pd

from quant.data.sources.daily._shared import eastmoney_index_kline
from quant.data.sources.daily.sina import SinaDailySource


class DefaultDailySource:
    name = "default"

    def __init__(self) -> None:
        from quant.data.sources.daily.akshare import AkshareDailySource

        self._ak = AkshareDailySource()
        self._sina = SinaDailySource()

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
        return self._sina.fetch_spot()
