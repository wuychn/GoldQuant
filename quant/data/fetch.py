"""日线库数据 facade：每个方法内部走 ``try_with_fallback``（数据驱动 fallback）。

业务层调 ``fetch_spot()`` / ``fetch_hist()`` 等零改动；fallback 顺序由 yml 决定
（单值=不 fallback，list=按序尝试），不在 facade 硬编码源名。

本模块不 import akshare/具体源。
"""

from __future__ import annotations

import pandas as pd  # 类型注解

# 进程级注入东财请求头 + curl_cffi 统一层。
from common.utils.source_headers import apply_source_header_patch

apply_source_header_patch()


def fetch_hist(code: str, *, start: str, end: str, adjust: str = "") -> pd.DataFrame:
    from quant.data.sources.interface import try_with_fallback

    return try_with_fallback("daily", "fetch_hist", code=code, start=start, end=end, adjust=adjust)


def fetch_index(code: str = "000300", *, start: str, end: str) -> pd.DataFrame:
    from quant.data.sources.interface import try_with_fallback

    return try_with_fallback("daily", "fetch_index", code=code, start=start, end=end)


def fetch_trade_calendar() -> list[str]:
    from quant.data.sources.interface import try_with_fallback

    return try_with_fallback("daily", "fetch_calendar")


def fetch_a_code_name() -> list[str]:
    from quant.data.sources.interface import try_with_fallback

    return try_with_fallback("daily", "fetch_code_list")


def fetch_spot() -> pd.DataFrame:
    from quant.data.sources.interface import try_with_fallback

    return try_with_fallback("daily", "fetch_spot")


def fetch_delisted_codes() -> pd.DataFrame:
    from quant.data.sources.interface import try_with_fallback

    return try_with_fallback("daily", "fetch_delisted_codes")


def fetch_delisted_daily(code: str, *, start: str, end: str) -> pd.DataFrame:
    from quant.data.sources.interface import try_with_fallback

    return try_with_fallback("daily", "fetch_delisted_daily", code=code, start=start, end=end)


# 兼容旧名（已废弃，新代码用 fetch_spot）
fetch_spot_em = fetch_spot
