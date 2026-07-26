"""AKShare 行情拉取与字段归一化（供 build/update 脚本调用）。

把 spot_em / stock_zh_a_hist / index_zh_a_hist 的返回归一化到 daily_raw / index_daily schema。
"""

from __future__ import annotations

import pandas as pd

from quant.data.schema import HIST_FIELD_MAP, INDEX_DAILY_COLUMNS, SPOT_EM_FIELD_MAP


def fetch_spot_em() -> pd.DataFrame:
    """全市场实时行情 → daily_raw 列（不含 date，由调用方补当日）。"""
    import akshare as ak

    df = ak.stock_zh_a_spot_em()
    return _normalize_spot(df)


def _normalize_spot(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=list(SPOT_EM_FIELD_MAP.values()))
    out = pd.DataFrame()
    for src, dst in SPOT_EM_FIELD_MAP.items():
        if src in df.columns:
            out[dst] = df[src]
    for c in ("open", "high", "low", "close", "pre_close", "volume", "amount",
              "turnover_rate", "float_mv", "total_mv"):
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    out["code"] = out["code"].astype(str).str.strip()
    return out


def fetch_hist(code: str, *, start: str, end: str, adjust: str = "") -> pd.DataFrame:
    """单只历史日线 → daily_raw 列（含 date）。``adjust`` 默认不复权。"""
    import akshare as ak

    df = ak.stock_zh_a_hist(
        symbol=str(code),
        period="daily",
        start_date=start.replace("-", ""),
        end_date=end.replace("-", ""),
        adjust=adjust,
    )
    return _normalize_hist(df, code)


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


def fetch_index(code: str = "000300", *, start: str, end: str) -> pd.DataFrame:
    """指数日线 → index_daily 列。"""
    import akshare as ak

    df = ak.index_zh_a_hist(
        symbol=str(code),
        period="daily",
        start_date=start.replace("-", ""),
        end_date=end.replace("-", ""),
    )
    if df is None or df.empty:
        return pd.DataFrame(columns=list(INDEX_DAILY_COLUMNS))
    out = pd.DataFrame()
    col_map = {
        "日期": "date", "开盘": "open", "最高": "high", "最低": "low",
        "收盘": "close", "成交量": "volume", "成交额": "amount",
    }
    for src, dst in col_map.items():
        if src in df.columns:
            out[dst] = df[src]
    out["code"] = str(code)
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    for c in ("open", "high", "low", "close", "volume", "amount"):
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out[list(INDEX_DAILY_COLUMNS)]


def fetch_trade_calendar() -> list[str]:
    import akshare as ak

    df = ak.tool_trade_date_hist_sina()
    col = None
    for c in df.columns:
        if "date" in str(c).lower() or "日期" in str(c):
            col = c
            break
    if col is None:
        return []
    return sorted(pd.to_datetime(df[col]).dt.strftime("%Y-%m-%d").tolist())
