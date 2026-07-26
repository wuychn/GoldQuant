"""Point-in-time universe 构建.

规则（全部 PIT，可对任意历史日 T 重建，且只依赖 T 及之前信息）：
1. 剔除 ST/ST带星/退/PT —— 名称字符串匹配（名称随每日快照落库，天然 PIT）
2. 剔除上市不足 ``min_list_days`` 交易日 —— 由 daily_raw 首条记录推断
3. 剔除当日停牌 —— volume == 0
4. 20 日 ADV >= ``min_adv_yi`` —— 由 amount 滚动计算

universe 快照按日落库（date, code, name, included），回测时直接读快照。
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from quant.data.schema import (
    DEFAULT_ADV_LOOKBACK,
    DEFAULT_MIN_ADV_YI,
    DEFAULT_MIN_LIST_DAYS,
    ST_NAME_MARKERS,
)
from quant.data.store import read_daily_raw, read_universe_snapshot, write_universe_snapshot
from quant.data.calendar import prev_trading_day, to_iso


def _is_st_name(name: str) -> bool:
    if not name:
        return False
    n = str(name).upper().strip()
    return any(m.upper() in n for m in ST_NAME_MARKERS)


def _coerce_iso(s: str):
    from datetime import date as _date

    try:
        if len(s) == 8 and s.isdigit():
            return _date(int(s[:4]), int(s[4:6]), int(s[6:]))
        return _date.fromisoformat(s[:10])
    except Exception:
        return None


def _listing_days_map(daily: pd.DataFrame, as_of: str) -> dict[str, int]:
    """截至 as_of，每只票已上市交易日数。"""
    if daily.empty:
        return {}
    d = daily[daily["date"] <= as_of]
    return d.groupby("code")["date"].count().to_dict()


def _adv_yi_map(daily: pd.DataFrame, as_of: str, lookback: int) -> dict[str, float]:
    """截至 as_of，每只票近 lookback 日平均成交额（元）。"""
    if daily.empty:
        return {}
    d = daily[daily["date"] <= as_of]
    out: dict[str, float] = {}
    for code, g in d.groupby("code"):
        g = g.sort_values("date").tail(lookback)
        if g.empty:
            continue
        amt = pd.to_numeric(g["amount"], errors="coerce").dropna()
        if amt.empty:
            continue
        out[code] = float(amt.mean())
    return out


def build_universe_snapshot(
    as_of: str,
    *,
    daily: pd.DataFrame | None = None,
    min_list_days: int = DEFAULT_MIN_LIST_DAYS,
    min_adv_yi: float = DEFAULT_MIN_ADV_YI,
    adv_lookback: int = DEFAULT_ADV_LOOKBACK,
) -> pd.DataFrame:
    """构建某交易日的 universe 快照（含全部在市票，included 标记是否通过过滤）。"""
    if daily is None:
        daily = read_daily_raw(end=as_of)
    if daily.empty:
        return pd.DataFrame(columns=["date", "code", "name", "included"])

    as_of = to_iso(as_of)
    # 当日有行情的票（在市且当日有数据）
    today_rows = daily[daily["date"] == as_of]
    if today_rows.empty:
        # as_of 非交易日或无数据：回退到最近的前一交易日（PIT，绝不跳到未来）
        from datetime import date as _date

        d_obj = _coerce_iso(as_of)
        prev = prev_trading_day(d_obj) if d_obj is not None else None
        if prev is not None:
            prev_iso = prev.isoformat()
            today_rows = daily[daily["date"] == prev_iso]
            as_of = prev_iso
        if today_rows.empty:
            return pd.DataFrame(columns=["date", "code", "name", "included"])

    list_days = _listing_days_map(daily, as_of)
    adv_map = _adv_yi_map(daily, as_of, adv_lookback)

    rows: list[dict] = []
    for _, r in today_rows.iterrows():
        code = str(r.get("code", "")).strip()
        name = str(r.get("name", "")).strip()
        if not code:
            continue
        included = True
        if _is_st_name(name):
            included = False
        elif list_days.get(code, 0) < min_list_days:
            included = False
        else:
            try:
                vol = float(r.get("volume", 0) or 0)
            except (TypeError, ValueError):
                vol = 0.0
            if vol <= 0:
                included = False  # 当日停牌/无成交
            else:
                adv = adv_map.get(code, 0.0)
                if adv / 1e8 < min_adv_yi:
                    included = False
        rows.append({"date": as_of, "code": code, "name": name, "included": included})

    return pd.DataFrame(rows, columns=["date", "code", "name", "included"])


def universe_codes(as_of: str, *, rebuild: bool = False, **kwargs) -> list[str]:
    """返回某交易日通过过滤的 universe 代码列表。

    优先读已落库快照；``rebuild=True`` 或无快照时即时构建并落库。
    """
    if not rebuild:
        snap = read_universe_snapshot(as_of)
        if not snap.empty:
            return snap.loc[snap["included"], "code"].astype(str).tolist()

    snap = build_universe_snapshot(as_of, **kwargs)
    if not snap.empty:
        write_universe_snapshot(snap)
    return snap.loc[snap["included"], "code"].astype(str).tolist()


def universe_snapshot(as_of: str, *, rebuild: bool = False, **kwargs) -> pd.DataFrame:
    """返回某交易日 universe 快照 DataFrame（含 included 标记）。"""
    if not rebuild:
        snap = read_universe_snapshot(as_of)
        if not snap.empty:
            return snap
    snap = build_universe_snapshot(as_of, **kwargs)
    if not snap.empty:
        write_universe_snapshot(snap)
    return snap
