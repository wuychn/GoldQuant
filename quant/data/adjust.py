"""复权因子：不复权 ↔ 后复权互转。

库里存不复权 OHLCV + 后复权因子 ``hfq_factor``。
后复权价 = 不复权价 × hfq_factor。
除权检测：spot_em 的 ``昨收`` 与库中前一交易日 ``close`` 不一致 → 除权，
仅对这几只重取因子（``stock_zh_a_daily(adjust='hfq-factor')``）。
"""

from __future__ import annotations

import pandas as pd

from quant.data.store import read_adj_factor, read_daily_raw, write_adj_factor


def detect_ex_dividend_codes(latest_spot: pd.DataFrame, prev_close_map: dict[str, float]) -> list[str]:
    """比对当日 spot_em 昨收 与 库中前一日 close，不一致者即除权。

    ``latest_spot`` 需含 code、pre_close 列；``prev_close_map`` 为 {code: 上一日 close}。
    """
    if latest_spot.empty:
        return []
    out: list[str] = []
    for _, r in latest_spot.iterrows():
        code = str(r.get("code", "")).strip()
        if not code:
            continue
        prev = prev_close_map.get(code)
        if prev is None or prev <= 0:
            continue
        try:
            spot_prev = float(r.get("pre_close", 0) or 0)
        except (TypeError, ValueError):
            continue
        if spot_prev <= 0:
            continue
        # 除权：昨收按比例调整，差异超 0.1% 视为除权（避免浮点噪声）
        if abs(spot_prev - prev) / prev > 0.001:
            out.append(code)
    return out


def fetch_hfq_factor(code: str) -> pd.DataFrame:
    """拉取单只后复权因子序列（新浪源，仅在除权时按需调用）。"""
    import akshare as ak

    df = ak.stock_zh_a_daily(symbol=_sina_symbol(code), adjust="hfq-factor")
    if df is None or df.empty:
        return pd.DataFrame(columns=["code", "date", "hfq_factor"])

    date_col = None
    factor_col = None
    for c in df.columns:
        cs = str(c)
        if "date" in cs.lower() or "日期" in cs:
            date_col = c
        if "factor" in cs.lower() or "复权" in cs:
            factor_col = c
    if date_col is None or factor_col is None:
        return pd.DataFrame(columns=["code", "date", "hfq_factor"])

    out = pd.DataFrame(
        {
            "code": code,
            "date": pd.to_datetime(df[date_col]).dt.strftime("%Y-%m-%d"),
            "hfq_factor": pd.to_numeric(df[factor_col], errors="coerce"),
        }
    )
    return out.dropna(subset=["hfq_factor"]).reset_index(drop=True)


def _sina_symbol(code: str) -> str:
    """6 位代码 → 新浪 sh/sz 前缀。"""
    c = str(code).strip()
    if c.startswith(("6", "9")):
        return f"sh{c}"
    if c.startswith(("0", "2", "3")):
        return f"sz{c}"
    if c.startswith(("8", "4")):
        return f"bj{c}"
    return f"sh{c}"


def apply_hfq(df: pd.DataFrame, adj: pd.DataFrame) -> pd.DataFrame:
    """把不复权日线按 hfq_factor 转为后复权。price 列 × factor。"""
    if df.empty or adj.empty:
        return df
    price_cols = ("open", "high", "low", "close", "pre_close")
    merged = df.merge(adj[["code", "date", "hfq_factor"]], on=["code", "date"], how="left")
    merged["hfq_factor"] = merged["hfq_factor"].fillna(1.0)
    for c in price_cols:
        if c in merged.columns:
            merged[c] = merged[c] * merged["hfq_factor"]
    return merged.drop(columns=["hfq_factor"])


def refresh_adj_for_codes(codes: list[str]) -> int:
    """对给定代码重拉后复权因子并落库；返回更新条数。"""
    if not codes:
        return 0
    frames = []
    for code in codes:
        try:
            frames.append(fetch_hfq_factor(code))
        except Exception:
            continue
    if not frames:
        return 0
    df = pd.concat(frames, ignore_index=True)
    write_adj_factor(df)
    return len(df)


def hfq_close_series(code: str, *, start: str | None = None, end: str | None = None) -> pd.Series | None:
    """取单只后复权收盘价序列（index=date）。"""
    raw = read_daily_raw(codes=[code], start=start, end=end)
    if raw.empty:
        return None
    adj = read_adj_factor(codes=[code])
    if adj.empty:
        return None
    merged = apply_hfq(raw, adj)
    s = merged.set_index("date")["close"].sort_index()
    return s
