"""东财涨停池：akshare ``stock_zt_pool_em(date)``，按交易日取截面。"""

from __future__ import annotations

import pandas as pd

from quant.data.zt_pool import normalize_zt_pool


def fetch_zt_pool_hist(*, date: str) -> pd.DataFrame:
    import akshare as ak

    as_of = str(date).strip()[:10]
    ymd = as_of.replace("-", "")[:8]
    df = ak.stock_zt_pool_em(date=ymd)
    return normalize_zt_pool(df, as_of=as_of)
