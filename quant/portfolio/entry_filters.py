"""入场过滤：挡住过热动量新开仓（不强制平已有仓）。"""

from __future__ import annotations

import pandas as pd


def overheat_codes(
    daily: pd.DataFrame,
    as_of: str,
    codes: list[str] | set[str],
    *,
    lookback: int = 5,
    max_ret: float = 0.15,
) -> set[str]:
    """返回在 ``as_of`` 日、过去 ``lookback`` 日收益 ≥ ``max_ret`` 的代码集合。

    ``max_ret`` 为小数（0.15 = +15%）。数据不足 lookback 根 K 线的代码不标记过热。
    """
    if daily is None or daily.empty or max_ret <= 0 or lookback <= 0:
        return set()
    want = {str(c) for c in codes}
    if not want:
        return set()
    d = daily
    if not pd.api.types.is_string_dtype(d["date"]):
        d = d.copy()
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
    as_of = str(as_of)[:10]
    # 只取候选 + as_of 以前，再按票取末 lookback+1 根，避免全历史逐票扫描
    sub = d.loc[(d["date"] <= as_of) & (d["code"].astype(str).isin(want)), ["code", "date", "close"]]
    if sub.empty:
        return set()
    need = lookback + 1
    last = sub.sort_values(["code", "date"]).groupby("code", sort=False).tail(need)
    out: set[str] = set()
    for code, g in last.groupby("code", sort=False):
        if len(g) < need:
            continue
        c0 = float(g["close"].iloc[0])
        c1 = float(g["close"].iloc[-1])
        if c0 <= 0 or c1 != c1 or c0 != c0:
            continue
        if c1 / c0 - 1.0 >= max_ret:
            out.add(str(code))
    return out


def filter_new_entries(
    alpha: dict[str, float],
    current: dict[str, float],
    hot: set[str],
) -> dict[str, float]:
    """从 alpha 中剔除过热且**尚未持仓**的代码；已持仓保留（交给出场/再平衡）。"""
    if not hot:
        return alpha
    held = {str(c) for c, w in (current or {}).items() if w and w > 0}
    return {c: v for c, v in alpha.items() if c in held or c not in hot}
