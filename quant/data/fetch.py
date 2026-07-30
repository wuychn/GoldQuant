"""日线库数据 facade：委托当前 DailySource（``quant.yml data.sources.daily`` 配置选）。

具体实现见 ``quant/data/sources/daily/``：
- ``default``：组合最优（fetch_index 直连东财 kline，其余 akshare），推荐。
- ``akshare``：全 akshare（fetch_index 走 clist 易断，仅对照）。

本模块**不再 import akshare**——换源改配置即可，下游（build_daily/maintain/backtest）零感知。
退市接口（fetch_delisted_codes/daily）仍在 ``quant/data/delist.py``，DailySource 内部委托之。
"""

from __future__ import annotations

import pandas as pd  # 类型注解

# 进程级注入东财请求头 + curl_cffi 统一层（须在首次拉数据前；build/update/maintain 与
# python -m quant 直跑不经 app lifespan，故在数据模块加载时打补丁）。
from common.utils.source_headers import apply_source_header_patch

apply_source_header_patch()


def _src():
    from quant.data.sources.factory import get_daily_source

    return get_daily_source()


def fetch_hist(code: str, *, start: str, end: str, adjust: str = "") -> pd.DataFrame:
    return _src().fetch_hist(code, start=start, end=end, adjust=adjust)


def fetch_index(code: str = "000300", *, start: str, end: str) -> pd.DataFrame:
    return _src().fetch_index(code, start=start, end=end)


def fetch_trade_calendar() -> list[str]:
    return _src().fetch_calendar()


def fetch_a_code_name() -> list[str]:
    return _src().fetch_code_list()


def fetch_spot_em() -> pd.DataFrame:
    return _src().fetch_spot()
