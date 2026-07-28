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

from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import to_iso
from quant.data.universe import universe_codes
from quant.factors.base import FactorRow
from quant.factors.library import BarSeries
from quant.factors.registry import FactorRegistry, REGISTRY


def _industry_map_for(as_of: str) -> dict[str, str]:
    """PIT 行业映射：只读落库快照；缺失返回空（中性化该日退化）。

    不再用 ``fetch_current_industry_map`` 回填历史——那会把"今天的"行业分类用于
    历史日期，在个股主业转型 / 申万分类调整时构成 look-ahead。
    """
    from quant.data.industry import read_industry_snapshot

    return read_industry_snapshot(as_of) or {}


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


def _build_bar_series(code_df: pd.DataFrame, code: str) -> BarSeries | None:
    """从单只票的后复权 DataFrame 构 BarSeries。

    code_df 已含该票全部历史，且**已在上游合并复权因子**（见 build_panel KEYSTONE）；
    本函数不再二次复权。
    """
    if code_df.empty:
        return None
    df = code_df.sort_values("date").set_index("date")
    cols = [c for c in ("open", "high", "low", "close", "volume", "amount", "turnover_rate", "float_mv") if c in df.columns]
    return BarSeries(code=code, df=df[cols])


def build_panel(
    dates: list[str],
    *,
    registry: FactorRegistry = REGISTRY,
    industries: dict[str, str] | None = None,
    daily: pd.DataFrame | None = None,
    adj: pd.DataFrame | None = None,
    rebuild_universe: bool = False,
    universe_by_date: dict[str, list[str]] | None = None,
) -> list[FactorRow]:
    """构建全历史因子面板。返回 FactorRow 列表（含 raw/neutral/forward_return_pct/meta['fwd']）。

    【KEYSTONE】``daily`` 必须为**后复权**帧：默认经 ``load_adjusted_daily`` 读取
    （不复权 raw + adj_factor 合并）；调用方自行传入时须已复权。因子值与前瞻收益/IC
    因此在同一复权基准上，消除除权日假跳空（修此前 IC 标签被原始价污染的 bug）。
    ``adj`` 参数已废弃：调整在加载边界完成，面板内不再二次复权。
    ``dates`` 为要构建的交易日列表（ISO 或 YYYYMMDD 均可，内部归一化为 ISO）。
    ``universe_by_date`` 可直接注入每日 universe，跳过离线库查询（测试/复用场景）。
    """
    if daily is None:
        daily = load_adjusted_daily()
    daily = _normalize_daily(daily)

    factors = registry.all()
    iso_dates = [to_iso(d) for d in dates]
    date_set = set(iso_dates)
    universe_map = _resolve_universe(iso_dates, rebuild_universe, universe_by_date)
    # 按评估日缓存 PIT 行业（同一进程内复用）
    industry_by_date: dict[str, dict[str, str]] = {}
    if industries is not None:
        for d in iso_dates:
            industry_by_date[d] = industries
    else:
        for d in iso_dates:
            industry_by_date[d] = _industry_map_for(d)

    # 按 code 分组，一次性建 BarSeries 并复用
    # 预先按 (code, date) 排序：下面用 searchsorted 定位评估日，依赖 date 升序
    daily = daily.sort_values(["code", "date"], kind="stable")

    rows: list[FactorRow] = []
    for code, code_df in daily.groupby("code", sort=False):
        if code_df.empty:
            continue
        bars = _build_bar_series(code_df, code)
        if bars is None:
            continue
        # 该 code 在哪些评估日有数据
        code_dates = set(code_df["date"].tolist()) & date_set
        if not code_dates:
            continue
        # 该 code 在 universe 中的评估日
        universe_needed = sorted(d for d in code_dates if code in universe_map.get(d, ()))
        if not universe_needed:
            continue

        # 预取按 date 升序的数组，评估日用 searchsorted 定位「<= d 的最后一行」
        date_arr = code_df["date"].to_numpy(dtype=object)
        mv_arr = (
            pd.to_numeric(code_df["float_mv"], errors="coerce").to_numpy(dtype=float)
            if "float_mv" in code_df.columns
            else None
        )
        name_arr = code_df["name"].to_numpy(dtype=object) if "name" in code_df.columns else None

        for d in universe_needed:
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
            # 「<= d」的最后一行位置（date_arr 已升序）
            pos = int(np.searchsorted(date_arr, d, side="right")) - 1
            lm = None
            if mv_arr is not None and pos >= 0:
                val = mv_arr[pos]
                if np.isfinite(val) and val > 0:
                    lm = float(np.log(val))
            nm = str(name_arr[pos]) if name_arr is not None and pos >= 0 else ""
            fr = FactorRow(
                date=d,
                code=code,
                name=nm,
                industry=industry_by_date.get(d, {}).get(code, ""),
                log_mcap=lm,
                raw=raw,
            )
            rows.append(fr)

    # PIT 快照 → flow / hot / theme / fundamentals
    _inject_snapshot_factors(rows, iso_dates)
    # 行业动量代理 → theme_mom（快照缺失时回退）
    _fill_theme_mom(rows, overwrite=False)

    # 截面中性化（含 winsorize）
    from quant.factors.neutralize import neutralize_panel_rows

    neutralize_panel_rows(rows, factor_names=registry.names())

    # 前瞻收益（IC 用）：向量化按 code shift，按 (date, code) 索引
    fwd = _forward_returns_panel(daily, {r.code for r in rows}, date_set, horizons=(1, 3, 5, 10, 20))
    for r in rows:
        fmap = fwd.get((r.date, r.code), {})
        r.forward_return_pct = fmap.get(5)  # 主用 5 日
        r.meta["fwd"] = dict(fmap)
    return rows


def _fill_theme_mom(rows: list[FactorRow], *, overwrite: bool = True) -> None:
    """用同日同行业 mom_20 均值的分位填充 theme_mom（无板块日线时的代理）。"""
    from collections import defaultdict

    by_date: dict[str, list[FactorRow]] = defaultdict(list)
    for r in rows:
        by_date[r.date].append(r)
    for _d, grp in by_date.items():
        ind_moms: dict[str, list[float]] = defaultdict(list)
        for r in grp:
            if not overwrite and r.raw.get("theme_mom") is not None:
                continue
            m = r.raw.get("mom_20")
            if m is not None and r.industry:
                ind_moms[r.industry].append(float(m))
        if len(ind_moms) < 2:
            continue
        ind_mean = {k: float(np.mean(v)) for k, v in ind_moms.items()}
        vals = sorted(ind_mean.values())
        n = len(vals)
        for r in grp:
            if not overwrite and r.raw.get("theme_mom") is not None:
                continue
            if not r.industry or r.industry not in ind_mean:
                continue
            v = ind_mean[r.industry]
            rank = sum(1 for x in vals if x <= v) / n
            r.raw["theme_mom"] = rank


def _inject_snapshot_factors(rows: list[FactorRow], iso_dates: list[str]) -> None:
    from quant.data.factor_snapshots import read_fund_flow_snapshot, read_hot_rank_snapshot, read_theme_snapshot
    from quant.data.fundamentals import read_fundamentals_snapshot

    snap_cache: dict[str, tuple] = {}
    for r in rows:
        d = r.date
        if d not in snap_cache:
            snap_cache[d] = (
                read_fund_flow_snapshot(d),
                read_hot_rank_snapshot(d),
                read_theme_snapshot(d),
                read_fundamentals_snapshot(d),
            )
        flow_snap, hot_snap, theme_snap, fund_snap = snap_cache[d]
        if "flow_ratio_5" not in r.raw:
            v = flow_snap.get(r.code)
            if v is not None:
                r.raw["flow_ratio_5"] = float(v)
        if "hot_rank_z" not in r.raw:
            hv = hot_snap.get(r.code)
            if hv is not None:
                r.raw["hot_rank_z"] = float(hv)
        if "theme_mom" not in r.raw:
            tv = theme_snap.get(r.code)
            if tv is not None:
                r.raw["theme_mom"] = float(tv)
        fb = fund_snap.get(r.code) or {}
        if fb.get("pe_ttm") is not None:
            pe = float(fb["pe_ttm"])
            r.raw.setdefault("pe_ttm", pe)
            if pe > 0:
                r.raw.setdefault("ep_ttm", 1.0 / pe)
        if fb.get("pb") is not None:
            pb = float(fb["pb"])
            r.raw.setdefault("pb", pb)
            if pb > 0:
                r.raw.setdefault("bp", 1.0 / pb)
        if fb.get("roe") is not None:
            r.raw.setdefault("roe", float(fb["roe"]))
        if fb.get("rev_yoy") is not None:
            r.raw.setdefault("rev_yoy", float(fb["rev_yoy"]))


def _resolve_universe(
    iso_dates: list[str], rebuild: bool, override: dict[str, list[str]] | None
) -> dict[str, set[str]]:
    """一次性解析每个评估日的 universe，返回 {date: set(codes)}。

    调用级缓存（非模块级全局）：避免跨调用状态污染与无界增长。
    ``override`` 允许直接注入（测试或已有快照场景），跳过离线库查询。
    """
    out: dict[str, set[str]] = {}
    for d in iso_dates:
        if override is not None:
            out[d] = set(override.get(d, ()))
        else:
            out[d] = set(universe_codes(d, rebuild=rebuild))
    return out


def _forward_returns_panel(
    daily: pd.DataFrame, codes: set[str], date_set: set[str], horizons=(1, 3, 5, 10, 20)
) -> dict[tuple[str, str], dict[int, float]]:
    """按 code 向量化算多档前瞻收益。返回 {(date, code): {horizon: pct}}。

    键必须含 date：同一只票在不同评估日的前瞻收益不同，按 code 聚合会让
    后一个评估日覆盖前一个，导致全部 FactorRow 拿到同一份值、IC 分析失效。
    """
    out: dict[tuple[str, str], dict[int, float]] = {}
    if daily.empty:
        return out
    for code, g in daily[daily["code"].isin(codes)].groupby("code"):
        g = g.sort_values("date").reset_index(drop=True)
        closes = pd.to_numeric(g["close"], errors="coerce")
        dcol = g["date"].astype(str)
        eval_mask = dcol.isin(date_set).values
        if not eval_mask.any():
            continue
        base = closes.to_numpy(dtype=float)
        # 各档前瞻价：shift(-h) 的向量化等价
        fwd_arrays: dict[int, np.ndarray] = {}
        n = len(base)
        for h in horizons:
            arr = np.full(n, np.nan)
            if h < n:
                arr[: n - h] = base[h:]
            fwd_arrays[h] = arr
        for pos in np.where(eval_mask)[0]:
            a = base[pos]
            if not np.isfinite(a) or a <= 0:
                continue
            hd: dict[int, float] = {}
            for h in horizons:
                b = fwd_arrays[h][pos]
                if np.isfinite(b) and b > 0:
                    hd[h] = float((b / a - 1.0) * 100.0)
            if hd:
                out[(str(dcol.iloc[pos]), code)] = hd
    return out
