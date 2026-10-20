"""东财涨停板族：炸板 / 跌停 / 昨日涨停。公开接口历史很短，按日落盘。"""

from __future__ import annotations

import pandas as pd


def _ymd(date: str) -> str:
    return str(date).strip().replace("-", "")[:8]


def fetch_zt_zb_pool(*, date: str) -> pd.DataFrame:
    import akshare as ak

    try:
        df = ak.stock_zt_pool_zbgc_em(date=_ymd(date))
    except Exception:
        return pd.DataFrame()
    return df if df is not None else pd.DataFrame()


def fetch_zt_dt_pool(*, date: str) -> pd.DataFrame:
    import akshare as ak

    try:
        df = ak.stock_zt_pool_dtgc_em(date=_ymd(date))
    except Exception:
        return pd.DataFrame()
    return df if df is not None else pd.DataFrame()


def fetch_zt_prev_pool(*, date: str) -> pd.DataFrame:
    import akshare as ak

    try:
        df = ak.stock_zt_pool_previous_em(date=_ymd(date))
    except Exception:
        return pd.DataFrame()
    return df if df is not None else pd.DataFrame()
