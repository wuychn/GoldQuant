"""退市股数据回填（Phase 4）：消除幸存者偏差的数据入口。

- ``fetch_delisted_codes``：akshare 退市清单（``stock_zh_a_stop_em``，回退沪深交所接口）。
- ``fetch_delisted_daily``：Sina ``stock_zh_a_daily(adjust="")`` 拉退市股**不复权**历史，
  归一化到 ``daily_raw`` schema（与在市股一致）；复权因子用 ``adjust.fetch_hfq_factor`` 另拉。

设计要点：退市股落库后，``universe.py`` 的 PIT 过滤（ST 名称 + ``volume==0`` 停牌）会在
退市日后自然将其剔除，而**历史在域内的日期保留**——这正是修复幸存者偏差所需。

akshare 接口名/字段随版本漂移（pin 1.18.79），归一化为纯函数便于单测；网络调用薄且防御。
"""

from __future__ import annotations

import pandas as pd

from quant.data.adjust import _sina_symbol
from quant.data.fetch import _retry
from quant.data.schema import HIST_FIELD_MAP


def _normalize_ohlc(df: pd.DataFrame, code: str) -> pd.DataFrame:
    """通用 OHLCV 归一化：中文列 → daily_raw schema（复用 HIST_FIELD_MAP）。"""
    if df is None or df.empty:
        return pd.DataFrame(
            columns=["code", "date", "name", "open", "high", "low", "close", "volume", "amount", "turnover_rate"]
        )
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


def _normalize_stop_df(df: pd.DataFrame) -> pd.DataFrame:
    """退市清单归一化 → columns: code, name, delist_date。纯函数，便于单测。"""
    if df is None or df.empty:
        return pd.DataFrame(columns=["code", "name", "delist_date"])
    code_col = next((c for c in df.columns if "代码" in str(c)), None)
    name_col = next((c for c in df.columns if "名称" in str(c)), None)
    date_col = next((c for c in df.columns if "日期" in str(c) or "时间" in str(c)), None)
    out = pd.DataFrame()
    if code_col is None:
        return pd.DataFrame(columns=["code", "name", "delist_date"])
    out["code"] = df[code_col].astype(str).str.strip()
    out["name"] = df[name_col].astype(str) if name_col else ""
    if date_col is not None:
        out["delist_date"] = pd.to_datetime(df[date_col], errors="coerce").dt.strftime("%Y-%m-%d")
    else:
        out["delist_date"] = ""
    return out.dropna(subset=["code"])


def fetch_delisted_codes() -> pd.DataFrame:
    """退市清单 → DataFrame[code, name, delist_date]。多源回退，失败返回空。"""
    import akshare as ak

    sources = [
        lambda: ak.stock_zh_a_stop_em(),
        lambda: ak.stock_info_sh_delist(),
        lambda: ak.stock_info_sz_delist(),
    ]
    for fn in sources:
        try:
            df = _retry(fn, label="delisted_codes", retries=2)
            norm = _normalize_stop_df(df)
            if not norm.empty:
                return norm
        except Exception:
            continue
    return pd.DataFrame(columns=["code", "name", "delist_date"])


def fetch_delisted_daily(code: str, *, start: str, end: str) -> pd.DataFrame:
    """Sina 拉单只退市股不复权日线 → daily_raw schema。失败返回空帧。"""
    import akshare as ak

    try:
        df = _retry(
            lambda: ak.stock_zh_a_daily(symbol=_sina_symbol(code), adjust=""),
            label=f"delisted_daily {code}",
            retries=2,
        )
    except Exception:
        return pd.DataFrame()
    norm = _normalize_ohlc(df, code)
    if norm.empty or "date" not in norm:
        return norm
    if start:
        norm = norm[norm["date"] >= start[:10]]
    if end:
        norm = norm[norm["date"] <= end[:10]]
    return norm.reset_index(drop=True)
