"""因子面板批量构建：从离线库 daily_raw + adj_factor 构建全历史 FactorRow 面板。

对每个交易日 T、每个 universe(T) 代码：
1. 取后复权 OHLCV（<= T）
2. 算全部因子 → raw
3. 行业 + log 市值填入 FactorRow
4. 截面中性化（neutralize_panel_rows）→ neutral
5. 前瞻收益（1/3/5/10/20 日）→ forward_return_pct + meta['fwd']（供 ic_decay）

性能：按 code 一次性建 BarSeries 并复用；前瞻收益向量化 shift。
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

from quant.data.adjust import apply_hfq, read_adj_factor
from quant.data.calendar import to_iso
from quant.data.store import read_daily_raw
from quant.data.universe import universe_codes
from quant.factors.base import FactorRow
from quant.factors.library import BarSeries
from quant.factors.registry import FactorRegistry, REGISTRY


def _industry_map() -> dict[str, str]:
    """代码 → 东财一级行业。复用 app 层映射；失败返回空。

    注意：当前实现取「当前」行业映射，写入历史 FactorRow.industry，中性化回归用了
    非 PIT 的行业哑变量。这是已知限制；离线库未落历史行业快照前，行业中性化退化为
    仅市值中性 + 当前行业近似。需严格 PIT 时改用落库历史行业快照。
    """
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


def _normalize_daily(daily: pd.DataFrame) -> pd.DataFrame:
    """确保 daily['date'] 为 ISO 字符串，便于与 as_of 字符串比较。"""
    if daily.empty:
        return daily
    if not pd.api.types.is_string_dtype(daily["date"]):
        daily = daily.copy()
        daily["date"] = pd.to_datetime(daily["date"]).dt.strftime("%Y-%m-%d")
    else:
        # 统一成 ISO（处理可能混入的 YYYYMMDD）
        sample = str(daily["date"].iloc[0]) if len(daily) else ""
        if len(sample) == 8 and sample.isdigit():
            daily = daily.copy()
            daily["date"] = pd.to_datetime(daily["date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
    return daily


def _build_bar_series(code_df: pd.DataFrame, adj: pd.DataFrame, code: str) -> BarSeries | None:
    """从单只票的 DataFrame 构后复权 BarSeries。code_df 已含该票全部历史。"""
    if code_df.empty:
        return None
    sub = code_df
    if not adj.empty:
        a = adj[adj["code"] == code]
        if not a.empty:
            sub = apply_hfq(sub, a)
    df = sub.sort_values("date").set_index("date")
    cols = [c for c in ("open", "high", "low", "close", "volume", "amount", "turnover_rate") if c in df.columns]
    return BarSeries(code=code, df=df[cols])


def build_panel(
    dates: list[str],
    *,
    registry: FactorRegistry = REGISTRY,
    industries: dict[str, str] | None = None,
    daily: pd.DataFrame | None = None,
    adj: pd.DataFrame | None = None,
    rebuild_universe: bool = False,
) -> list[FactorRow]:
    """构建全历史因子面板。返回 FactorRow 列表（含 raw/neutral/forward_return_pct/meta['fwd']）。

    ``dates`` 为要构建的交易日列表（ISO 或 YYYYMMDD 均可，内部归一化为 ISO）。
    """
    if daily is None:
        daily = read_daily_raw()
    daily = _normalize_daily(daily)
    if adj is None:
        adj = read_adj_factor()
    if industries is None:
        industries = _industry_map()

    factors = registry.all()
    iso_dates = [to_iso(d) for d in dates]
    date_set = set(iso_dates)

    # 按 code 分组，一次性建 BarSeries 并复用
    rows: list[FactorRow] = []
    for code, code_df in daily.groupby("code"):
        if code_df.empty:
            continue
        bars = _build_bar_series(code_df, adj, code)
        if bars is None:
            continue
        # 该 code 在哪些评估日有数据
        code_dates = set(code_df["date"].tolist()) & date_set
        if not code_dates:
            continue
        # 该 code 在 universe 中的评估日
        universe_needed = {d for d in iso_dates if d in code_dates and code in _universe_cache(d, rebuild_universe)}
        if not universe_needed:
            continue
        # log 市值按评估日取最近
        log_mcap_series = None
        if "float_mv" in code_df.columns:
            mv = pd.to_numeric(code_df["float_mv"], errors="coerce")
            log_mcap_series = np.log(mv.where(mv > 0))
        name_series = code_df.get("name")

        for d in sorted(universe_needed):
            raw: dict[str, float] = {}
            for f in factors:
                try:
                    v = f.compute(bars, d)
                except Exception:
                    v = None
                if v is not None and np.isfinite(v):
                    raw[f.name] = float(v) * f.direction
            if not raw:
                continue
            # log mcap
            lm = None
            if log_mcap_series is not None:
                sub_mv = code_df.loc[code_df["date"] <= d, "float_mv"]
                if not sub_mv.empty:
                    val = pd.to_numeric(sub_mv.iloc[-1], errors="coerce")
                    if np.isfinite(val) and val > 0:
                        lm = float(np.log(val))
            # name
            nm = ""
            if name_series is not None:
                sub_n = code_df.loc[code_df["date"] <= d, "name"]
                if not sub_n.empty:
                    nm = str(sub_n.iloc[-1])
            fr = FactorRow(
                date=d,
                code=code,
                name=nm,
                industry=industries.get(code, ""),
                log_mcap=lm,
                raw=raw,
            )
            rows.append(fr)

    # 截面中性化
    from quant.factors.neutralize import neutralize_panel_rows

    neutralize_panel_rows(rows, factor_names=registry.names())

    # 前瞻收益（IC 用）：向量化按 code shift
    fwd_by_code = _forward_returns_by_code(daily, {r.code for r in rows}, date_set, horizons=(1, 3, 5, 10, 20))
    for r in rows:
        fmap = fwd_by_code.get(r.code, {})
        r.forward_return_pct = fmap.get(5)  # 主用 5 日
        r.meta["fwd"] = {h: fmap[h] for h in (1, 3, 5, 10, 20) if h in fmap}
    return rows


_universe_cache_store: dict[str, list[str]] = {}


def _universe_cache(d: str, rebuild: bool) -> list[str]:
    if not rebuild and d in _universe_cache_store:
        return _universe_cache_store[d]
    codes = universe_codes(d, rebuild=rebuild)
    _universe_cache_store[d] = codes
    return codes


def _forward_returns_by_code(
    daily: pd.DataFrame, codes: set[str], date_set: set[str], horizons=(1, 3, 5, 10, 20)
) -> dict[str, dict[int, float]]:
    """按 code 向量化算多档前瞻收益。返回 {code: {horizon: pct}}。"""
    out: dict[str, dict[int, float]] = {}
    if daily.empty:
        return out
    for code, g in daily[daily["code"].isin(codes)].groupby("code"):
        g = g.sort_values("date").reset_index(drop=True)
        closes = pd.to_numeric(g["close"], errors="coerce")
        dcol = g["date"].astype(str)
        per_code: dict[int, float] = {}
        # 对该 code 的每个评估日算各档前瞻；这里只取该 code 出现在面板里的评估日
        eval_idx = dcol.isin(date_set)
        if not eval_idx.any():
            continue
        # 用 shift 算各档
        shifted = {}
        for h in horizons:
            shifted[h] = closes.shift(-h)
        # 对每个评估日位置写入
        positions = np.where(eval_idx.values)[0]
        # 但每个 code 同一评估日只对应一行；存 {date: {h: pct}}
        by_date: dict[str, dict[int, float]] = {}
        for pos in positions:
            d = str(dcol.iloc[pos])
            a = float(closes.iloc[pos])
            if not np.isfinite(a) or a <= 0:
                continue
            hd = {}
            for h in horizons:
                b = shifted[h].iloc[pos]
                if np.isfinite(b) and b > 0:
                    hd[h] = (b / a - 1.0) * 100.0
            by_date[d] = hd
        # 合并：每个评估日只取一份
        for d, hd in by_date.items():
            out.setdefault(code, {}).update(hd)
    return out
