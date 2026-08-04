"""日线库数据 facade：每个方法（接口）独立选源，一个方法只走一个源。

经 ``get_daily_source()`` 返回的接口级代理路由——换源只改 ``quant.yml data.sources.daily``
（整源或接口级 dict），业务代码零改动。源未实现某接口抛 NotImplementedError。

本模块不 import akshare/具体源。
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
    return _src().fetch_hist(code=code, start=start, end=end, adjust=adjust)


def fetch_index(code: str = "000300", *, start: str, end: str) -> pd.DataFrame:
    return _src().fetch_index(code=code, start=start, end=end)


def fetch_trade_calendar() -> list[str]:
    return _src().fetch_calendar()


def fetch_a_code_name() -> list[str]:
    return _src().fetch_code_list()


def fetch_spot() -> pd.DataFrame:
    return _src().fetch_spot()


def fetch_delisted_codes() -> pd.DataFrame:
    return _src().fetch_delisted_codes()


def fetch_delisted_daily(code: str, *, start: str, end: str) -> pd.DataFrame:
    return _src().fetch_delisted_daily(code=code, start=start, end=end)


# 兼容旧名（与东财耦合的命名已废弃，仅留别名防遗漏调用方；新代码请用 fetch_spot）
fetch_spot_em = fetch_spot
