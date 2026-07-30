"""AkshareDailySource：全 akshare 实现的日线库数据源（对照/回归用）。

注意：``fetch_index`` 走 akshare ``index_zh_a_hist``（先请求 80.push2/clist，TLS 指纹反爬
易被断开）。生产默认用 ``DefaultDailySource``（fetch_index 直连东财 kline，稳）。
"""

from __future__ import annotations

import pandas as pd

from quant.data.schema import HIST_FIELD_MAP, INDEX_DAILY_COLUMNS, SPOT_EM_FIELD_MAP
from quant.data.sources.daily._shared import _retry


def _normalize_hist(df: pd.DataFrame, code: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["code", "date", "name", "open", "high", "low",
                                     "close", "volume", "amount", "turnover_rate"])
    out = pd.DataFrame()
    for src, dst in HIST_FIELD_MAP.items():
        if src in df.columns:
            out[dst] = df[src]
    if "code" not in out.columns or out["code"].isna().any():
        out["code"] = str(code).strip()
    out["code"] = out["code"].astype(str).str.strip()
    if "date" in out.columns:
        out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    for c in ("open", "high", "low", "close", "volume", "amount", "turnover_rate"):
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def _normalize_index(df: pd.DataFrame, code: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=list(INDEX_DAILY_COLUMNS))
    col_map = {"日期": "date", "开盘": "open", "最高": "high", "最低": "low",
               "收盘": "close", "成交量": "volume", "成交额": "amount"}
    out = pd.DataFrame()
    for src, dst in col_map.items():
        if src in df.columns:
            out[dst] = df[src]
    out["code"] = str(code)
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    for c in ("open", "high", "low", "close", "volume", "amount"):
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out[list(INDEX_DAILY_COLUMNS)]


def _normalize_spot(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=list(SPOT_EM_FIELD_MAP.values()))
    out = pd.DataFrame()
    for src, dst in SPOT_EM_FIELD_MAP.items():
        if src in df.columns:
            out[dst] = df[src]
    for c in ("open", "high", "low", "close", "pre_close", "volume", "amount",
              "turnover_rate", "float_mv", "total_mv", "pct", "vol_ratio", "speed"):
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    out["code"] = out["code"].astype(str).str.strip()
    return out


class AkshareDailySource:
    name = "akshare"

    def fetch_hist(self, code: str, *, start: str, end: str, adjust: str = "") -> pd.DataFrame:
        import akshare as ak

        df = _retry(
            lambda: ak.stock_zh_a_hist(
                symbol=str(code), period="daily",
                start_date=start.replace("-", ""), end_date=end.replace("-", ""), adjust=adjust,
            ),
            label=f"hist {code}", retries=3,
        )
        return _normalize_hist(df, code)

    def fetch_index(self, code: str, *, start: str, end: str) -> pd.DataFrame:
        import akshare as ak

        df = _retry(
            lambda: ak.index_zh_a_hist(
                symbol=str(code), period="daily",
                start_date=start.replace("-", ""), end_date=end.replace("-", ""),
            ),
            label=f"index {code}",
        )
        return _normalize_index(df, code)

    def fetch_calendar(self) -> list[str]:
        import akshare as ak

        df = _retry(lambda: ak.tool_trade_date_hist_sina(), label="calendar")
        col = None
        for c in df.columns:
            if "date" in str(c).lower() or "日期" in str(c):
                col = c
                break
        if col is None:
            return []
        return sorted(pd.to_datetime(df[col]).dt.strftime("%Y-%m-%d").tolist())

    def fetch_code_list(self) -> list[str]:
        import akshare as ak

        df = _retry(lambda: ak.stock_info_a_code_name(), label="a_code_name", retries=2)
        return df["code"].astype(str).str.strip().tolist()

    def fetch_delisted_codes(self) -> pd.DataFrame:
        from quant.data.delist import fetch_delisted_codes

        return fetch_delisted_codes()

    def fetch_delisted_daily(self, code: str, *, start: str, end: str) -> pd.DataFrame:
        from quant.data.delist import fetch_delisted_daily

        return fetch_delisted_daily(code, start=start, end=end)

    def fetch_spot(self) -> pd.DataFrame:
        import akshare as ak

        df = _retry(lambda: ak.stock_zh_a_spot_em(), label="spot_em")
        return _normalize_spot(df)
