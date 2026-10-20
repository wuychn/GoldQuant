"""日线量纲：成交量统一为股，成交额/市值为元。

东财 hist/spot 的成交量是「手」（100 股）；新浪 hist 与新浪 spot（已 ×100）是「股」。
用 ``amount / (volume × close)`` 判别：≈1 已是股，≈100 则是手，乘 100。
幂等：已对齐的行不会再乘。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SHARES_PER_LOT = 100
# amount/(volume×close)：手≈100，股≈1
_LOTS_RATIO_LO = 30.0
_LOTS_RATIO_HI = 300.0


def volume_amount_ratio(df: pd.DataFrame) -> pd.Series:
    """逐行 ``amount / (volume × close)``；无效行为 NaN。"""
    v = pd.to_numeric(df.get("volume"), errors="coerce")
    a = pd.to_numeric(df.get("amount"), errors="coerce")
    c = pd.to_numeric(df.get("close"), errors="coerce")
    ok = (v > 0) & (a > 0) & (c > 0)
    out = pd.Series(np.nan, index=df.index, dtype=float)
    out.loc[ok] = a.loc[ok] / (v.loc[ok] * c.loc[ok])
    return out.replace([np.inf, -np.inf], np.nan)


def lots_volume_mask(df: pd.DataFrame) -> pd.Series:
    """成交量按「手」记账的行。"""
    r = volume_amount_ratio(df)
    return r.ge(_LOTS_RATIO_LO) & r.le(_LOTS_RATIO_HI)


def align_volume_to_shares(df: pd.DataFrame) -> pd.DataFrame:
    """把按手记账的 ``volume`` 乘 100 变成股。无量价列或已是股则原样返回。"""
    if df is None or df.empty:
        return df
    if not {"volume", "amount", "close"}.issubset(df.columns):
        return df
    out = df.copy()
    mask = lots_volume_mask(out)
    if not bool(mask.any()):
        return out
    vol = pd.to_numeric(out["volume"], errors="coerce")
    out.loc[mask, "volume"] = vol.loc[mask] * SHARES_PER_LOT
    return out


def fill_pre_close(
    df: pd.DataFrame,
    *,
    seed: dict[str, float] | None = None,
) -> pd.DataFrame:
    """同 code 相邻行 ``pre_close = close[t-1]``，只补空值。

    ``seed`` 是跨年分区的上一年末日收盘，用来填每只票当年第一行。
    """
    if df is None or df.empty:
        return df
    out = df.copy()
    if "close" not in out.columns:
        return out
    if "pre_close" not in out.columns:
        out["pre_close"] = pd.NA
    out = out.sort_values(["code", "date"])
    missing = out["pre_close"].isna() | (out["pre_close"].astype(str) == "")
    if seed:
        first = out.groupby("code", sort=False).cumcount() == 0
        mapped = out["code"].astype(str).map(seed)
        take = first & missing & mapped.notna()
        out.loc[take, "pre_close"] = mapped.loc[take]
        missing = out["pre_close"].isna() | (out["pre_close"].astype(str) == "")
    c = pd.to_numeric(out["close"], errors="coerce")
    prev = out.assign(__c=c).groupby("code", sort=False)["__c"].shift(1)
    out.loc[missing, "pre_close"] = prev.loc[missing]
    return out


def last_close_by_code(df: pd.DataFrame) -> dict[str, float]:
    """每只票最后一行的正收盘价，供下一年 ``fill_pre_close`` 做 seed。"""
    if df is None or df.empty or "close" not in df.columns:
        return {}
    last = df.sort_values(["code", "date"]).groupby("code", sort=False).tail(1)
    close = pd.to_numeric(last["close"], errors="coerce")
    out: dict[str, float] = {}
    for code, val in zip(last["code"].astype(str), close):
        if pd.notna(val) and float(val) > 0:
            out[code] = float(val)
    return out


def fill_turnover_from_mv(df: pd.DataFrame) -> pd.DataFrame:
    """缺 ``turnover_rate`` 时用 ``amount / float_mv × 100``（百分数）补 NaN。"""
    if df is None or df.empty:
        return df
    if "amount" not in df.columns or "float_mv" not in df.columns:
        return df
    out = df.copy()
    if "turnover_rate" not in out.columns:
        out["turnover_rate"] = pd.NA
    amt = pd.to_numeric(out["amount"], errors="coerce")
    mv = pd.to_numeric(out["float_mv"], errors="coerce")
    tr = pd.to_numeric(out["turnover_rate"], errors="coerce")
    miss = tr.isna() | (out["turnover_rate"].astype(str) == "")
    ok = miss & amt.notna() & mv.notna() & (mv > 0) & (amt >= 0)
    out.loc[ok, "turnover_rate"] = amt.loc[ok] / mv.loc[ok] * 100.0
    return out
