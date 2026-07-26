"""因子面板批量构建：从离线库 daily_raw + adj_factor 构建全历史 FactorRow 面板。

对每个交易日 T、每个 universe(T) 代码：
1. 取后复权 OHLCV（<= T）
2. 算全部因子 → raw
3. 行业 + log 市值填入 FactorRow
4. 截面中性化（neutralize_panel_rows）→ neutral
5. 前瞻收益（1/3/5/10/20 日）→ forward_return_pct
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

from quant.data.adjust import apply_hfq, read_adj_factor
from quant.data.store import read_daily_raw
from quant.data.universe import universe_codes
from quant.factors.base import FactorRow
from quant.factors.library import BarSeries
from quant.factors.registry import FactorRegistry, REGISTRY


def _industry_map() -> dict[str, str]:
    """代码 → 东财一级行业。复用 app 层映射；失败返回空。"""
    try:
        from app.utils.industry_board_fetch import fetch_em_industry_board

        rows = fetch_em_industry_board()
        out: dict[str, str] = {}
        for r in rows or []:
            code = str(r.get("代码") or r.get("股票代码") or "").strip()
            ind = str(r.get("行业") or r.get("板块") or "").strip()
            if code and ind:
                out[code] = ind
        return out
    except Exception:
        return {}


def _build_bar_series(daily: pd.DataFrame, adj: pd.DataFrame, code: str) -> BarSeries | None:
    sub = daily[daily["code"] == code]
    if sub.empty:
        return None
    if not adj.empty:
        a = adj[adj["code"] == code]
        if not a.empty:
            sub = apply_hfq(sub, a)
    df = sub.sort_values("date").set_index("date")
    cols = [c for c in ("open", "high", "low", "close", "volume", "amount", "turnover_rate") if c in df.columns]
    return BarSeries(code=code, df=df[cols])


def _log_mcap(daily: pd.DataFrame, code: str, as_of: str) -> float | None:
    sub = daily[(daily["code"] == code) & (daily["date"] <= as_of)]
    if sub.empty:
        return None
    mv = pd.to_numeric(sub["float_mv"], errors="coerce").iloc[-1]
    if not np.isfinite(mv) or mv <= 0:
        return None
    return float(np.log(mv))


def _forward_returns(daily: pd.DataFrame, code: str, as_of: str, horizons=(1, 3, 5, 10, 20)) -> dict[int, float]:
    sub = daily[daily["code"] == code].sort_values("date")
    idx = sub.index[sub["date"] == as_of]
    if len(idx) == 0:
        return {}
    pos = idx[0]
    closes = pd.to_numeric(sub["close"], errors="coerce")
    out: dict[int, float] = {}
    for h in horizons:
        if pos + h < len(sub):
            a = float(closes.iloc[pos])
            b = float(closes.iloc[pos + h])
            if a > 0:
                out[h] = (b / a - 1.0) * 100.0
    return out


def build_panel(
    dates: list[str],
    *,
    registry: FactorRegistry = REGISTRY,
    industries: dict[str, str] | None = None,
    daily: pd.DataFrame | None = None,
    adj: pd.DataFrame | None = None,
    rebuild_universe: bool = False,
) -> list[FactorRow]:
    """构建全历史因子面板。返回 FactorRow 列表（含 raw/neutral/forward_return_pct）。

    ``dates`` 为要构建的交易日列表；每个日期取 universe(T) 代码。
    """
    if daily is None:
        daily = read_daily_raw()
    if adj is None:
        adj = read_adj_factor()
    if industries is None:
        industries = _industry_map()

    factors = registry.all()
    rows: list[FactorRow] = []
    for d in dates:
        codes = universe_codes(d, rebuild=rebuild_universe)
        if not codes:
            continue
        sub_daily = daily[daily["date"] <= d]
        for code in codes:
            bars = _build_bar_series(sub_daily, adj, code)
            if bars is None:
                continue
            raw: dict[str, float] = {}
            for f in factors:
                try:
                    v = f.compute(bars, d)
                except Exception:
                    v = None
                if v is not None and np.isfinite(v):
                    # 方向调整：direction=-1 取负，使「越大越看多」统一
                    raw[f.name] = float(v) * f.direction
            if not raw:
                continue
            fr = FactorRow(
                date=d,
                code=code,
                name=str(sub_daily.loc[sub_daily["code"] == code, "name"].iloc[-1] if not sub_daily[sub_daily["code"] == code].empty else ""),
                industry=industries.get(code, ""),
                log_mcap=_log_mcap(sub_daily, code, d),
                raw=raw,
            )
            rows.append(fr)

    # 截面中性化
    from quant.factors.neutralize import neutralize_panel_rows

    neutralize_panel_rows(rows, factor_names=registry.names())

    # 前瞻收益（IC 用）
    fwd = _forward_returns_batch(daily, rows, dates)
    for r in rows:
        r.forward_return_pct = fwd.get((r.date, r.code))
    return rows


def _forward_returns_batch(daily: pd.DataFrame, rows: list[FactorRow], dates: list[str]) -> dict[tuple[str, str], float]:
    """批量算 5 日前瞻收益（IC 主用），按 (date, code) 索引。"""
    out: dict[tuple[str, str], float] = {}
    if daily.empty:
        return out
    daily = daily.sort_values(["code", "date"])
    codes = sorted({r.code for r in rows})
    for code in codes:
        sub = daily[daily["code"] == code].reset_index(drop=True)
        closes = pd.to_numeric(sub["close"], errors="coerce")
        date_to_pos = {str(sub.loc[i, "date"]): i for i in range(len(sub))}
        for r in rows:
            if r.code != code:
                continue
            pos = date_to_pos.get(r.date)
            if pos is None or pos + 5 >= len(sub):
                continue
            a = float(closes.iloc[pos])
            b = float(closes.iloc[pos + 5])
            if a > 0:
                out[(r.date, r.code)] = (b / a - 1.0) * 100.0
    return out
