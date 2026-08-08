"""因子面板批量构建：从离线库 daily_raw + adj_factor 构建全历史 FactorRow 面板。

对每个交易日 T、每个 universe(T) 代码：
1. 取后复权 OHLCV（<= T）
2. 算全部因子 → raw
3. 行业 + log 市值填入 FactorRow
4. 截面中性化（neutralize_panel_rows）→ neutral
5. 前瞻收益（1/3/5/10/20 日）→ forward_return_pct + meta['fwd']（供 ic_decay）

性能：按 code 一次性建 BarSeries 并复用；前瞻收益向量化 shift。
``workers>1`` 时仅并行「单票 raw 因子」循环（ProcessPool）；截面步骤仍串行。
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from typing import Any

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


def _split_codes(codes: list[str], n_chunks: int) -> list[list[str]]:
    """将已排序的 codes 均分为 ``n_chunks`` 块（块数不超过 len(codes)）。"""
    if not codes:
        return []
    n = max(1, min(int(n_chunks), len(codes)))
    size, rem = divmod(len(codes), n)
    out: list[list[str]] = []
    i = 0
    for k in range(n):
        take = size + (1 if k < rem else 0)
        out.append(codes[i : i + take])
        i += take
    return [c for c in out if c]


def _compute_raw_rows_for_codes(
    codes: list[str],
    daily: pd.DataFrame,
    date_set: set[str],
    universe_map: dict[str, list[str]],
    industry_by_date: dict[str, dict[str, str]],
    pit_table: pd.DataFrame,
) -> list[FactorRow]:
    """按给定 codes（须已排序）计算 raw FactorRow；供单进程与 ProcessPool worker 共用。

    使用进程内 ``REGISTRY``（不 pickle 因子闭包）；PIT 表在 worker 内建索引。
    """
    from quant.data.fundamental_pit import PitIndex, metrics_as_of

    if not codes or daily is None or daily.empty:
        return []
    factors = REGISTRY.all()
    umap = {d: set(v) for d, v in universe_map.items()}
    pit_index = PitIndex.from_table(pit_table)
    by_code = {str(c): g for c, g in daily.groupby(daily["code"].astype(str), sort=False)}

    rows: list[FactorRow] = []
    for code in codes:
        code_df = by_code.get(code)
        if code_df is None or code_df.empty:
            continue
        bars = _build_bar_series(code_df, code)
        if bars is None:
            continue
        code_dates = set(code_df["date"].astype(str).tolist()) & date_set
        if not code_dates:
            continue
        universe_needed = sorted(d for d in code_dates if code in umap.get(d, ()))
        if not universe_needed:
            continue

        date_arr = code_df["date"].astype(str).to_numpy(dtype=object)
        close_arr = (
            pd.to_numeric(code_df["close"], errors="coerce").to_numpy(dtype=float)
            if "close" in code_df.columns
            else None
        )
        mv_arr = (
            pd.to_numeric(code_df["float_mv"], errors="coerce").to_numpy(dtype=float)
            if "float_mv" in code_df.columns
            else None
        )
        name_arr = code_df["name"].to_numpy(dtype=object) if "name" in code_df.columns else None

        for d in universe_needed:
            pos = int(np.searchsorted(date_arr, d, side="right")) - 1
            px = float(close_arr[pos]) if close_arr is not None and pos >= 0 else None
            fm = (
                float(mv_arr[pos])
                if mv_arr is not None and pos >= 0 and np.isfinite(mv_arr[pos])
                else None
            )
            pit_extras = metrics_as_of(code, d, close=px, float_mv=fm, index=pit_index)
            object.__setattr__(bars, "extras", dict(pit_extras))

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
            lm = None
            if mv_arr is not None and pos >= 0:
                val = mv_arr[pos]
                if np.isfinite(val) and val > 0:
                    lm = float(np.log(val))
            nm = str(name_arr[pos]) if name_arr is not None and pos >= 0 else ""
            rows.append(
                FactorRow(
                    date=d,
                    code=code,
                    name=nm,
                    industry=industry_by_date.get(d, {}).get(code, ""),
                    log_mcap=lm,
                    raw=raw,
                )
            )
    return rows


def _compute_raw_rows_chunk(payload: dict[str, Any]) -> list[FactorRow]:
    """ProcessPool 入口：单参数 payload，避免 Windows spawn 下多参 pickle 问题。"""
    return _compute_raw_rows_for_codes(
        payload["codes"],
        payload["daily"],
        set(payload["dates"]),
        payload["universe_map"],
        payload["industry_by_date"],
        payload["pit_table"],
    )


def build_panel(
    dates: list[str],
    *,
    registry: FactorRegistry = REGISTRY,
    industries: dict[str, str] | None = None,
    daily: pd.DataFrame | None = None,
    adj: pd.DataFrame | None = None,
    rebuild_universe: bool = False,
    universe_by_date: dict[str, list[str]] | None = None,
    workers: int = 1,
) -> list[FactorRow]:
    """构建全历史因子面板。返回 FactorRow 列表（含 raw/neutral/forward_return_pct/meta['fwd']）。

    【KEYSTONE】``daily`` 必须为**后复权**帧：默认经 ``load_adjusted_daily`` 读取
    （不复权 raw + adj_factor 合并）；调用方自行传入时须已复权。因子值与前瞻收益/IC
    因此在同一复权基准上，消除除权日假跳空（修此前 IC 标签被原始价污染的 bug）。
    ``adj`` 参数已废弃：调整在加载边界完成，面板内不再二次复权。
    ``dates`` 为要构建的交易日列表（ISO 或 YYYYMMDD 均可，内部归一化为 ISO）。
    ``universe_by_date`` 可直接注入每日 universe，跳过离线库查询（测试/复用场景）。
    ``workers``：按股票并行算 raw 因子的进程数；``<=1`` 单进程（与默认口径一致）。
    截面中性化 / 快照 / 前瞻收益始终在主进程串行。
    """
    del adj  # 废弃参数，保留签名兼容
    workers = max(1, int(workers))
    if daily is None:
        daily = load_adjusted_daily()
    daily = _normalize_daily(daily)

    iso_dates = [to_iso(d) for d in dates]
    date_set = set(iso_dates)
    universe_map = _resolve_universe(iso_dates, rebuild_universe, universe_by_date)
    industry_by_date: dict[str, dict[str, str]] = {}
    if industries is not None:
        for d in iso_dates:
            industry_by_date[d] = industries
    else:
        for d in iso_dates:
            industry_by_date[d] = _industry_map_for(d)

    # 预先按 (code, date) 排序：searchsorted 依赖 date 升序；codes 排序保证确定性
    daily = daily.sort_values(["code", "date"], kind="stable")
    daily = daily.assign(code=daily["code"].astype(str))
    codes = sorted(daily["code"].unique().tolist())

    from quant.data.fundamental_pit import read_fundamental_pit_table

    pit_table = read_fundamental_pit_table()
    universe_map_ser = {d: sorted(v) for d, v in universe_map.items()}

    if workers <= 1 or len(codes) <= 1:
        rows = _compute_raw_rows_for_codes(
            codes,
            daily,
            date_set,
            universe_map_ser,
            industry_by_date,
            pit_table,
        )
    else:
        chunks = _split_codes(codes, workers)
        payloads = []
        for chunk in chunks:
            sub = daily[daily["code"].isin(chunk)]
            pit_sub = (
                pit_table[pit_table["code"].astype(str).isin(chunk)]
                if pit_table is not None and not pit_table.empty and "code" in pit_table.columns
                else pit_table
            )
            payloads.append(
                {
                    "codes": chunk,
                    "daily": sub,
                    "dates": iso_dates,
                    "universe_map": universe_map_ser,
                    "industry_by_date": industry_by_date,
                    "pit_table": pit_sub,
                }
            )
        rows = []
        with ProcessPoolExecutor(max_workers=len(chunks)) as pool:
            # map 保序：与 chunks 顺序一致
            for part in pool.map(_compute_raw_rows_chunk, payloads):
                rows.extend(part)

    rows.sort(key=lambda r: (r.date, r.code))

    # PIT 快照 → flow / hot / theme（基本面见 raw 循环）
    _inject_snapshot_factors(rows, iso_dates)
    _fill_theme_mom(rows, overwrite=False)

    from quant.factors.neutralize import neutralize_panel_rows

    neutralize_panel_rows(rows, factor_names=registry.names())

    fwd = _forward_returns_panel(daily, {r.code for r in rows}, date_set, horizons=(1, 3, 5, 10, 20))
    for r in rows:
        fmap = fwd.get((r.date, r.code), {})
        r.forward_return_pct = fmap.get(5)
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
    """hot/flow/theme 快照注入；基本面仅来自 fundamental_pit（见 build_panel 循环）。"""
    from quant.data.factor_snapshots import read_fund_flow_snapshot, read_hot_rank_snapshot, read_theme_snapshot

    snap_cache: dict[str, tuple] = {}
    for r in rows:
        d = r.date
        if d not in snap_cache:
            snap_cache[d] = (
                read_fund_flow_snapshot(d),
                read_hot_rank_snapshot(d),
                read_theme_snapshot(d),
            )
        flow_snap, hot_snap, theme_snap = snap_cache[d]
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
