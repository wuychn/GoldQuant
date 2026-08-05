"""上市日期 PIT 表：优先交易所/jbxx 真实上市日，回退 daily_raw 首条记录。

落库 ``store/listing_dates.parquet``（code, listing_date），universe 用
``trading_days_between(listing_date, as_of)`` 计上市天数，避免数据扩容时
静默改变 universe。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from quant.data.calendar import trading_days_between, to_iso
from quant.store.paths import quant_home


def listing_table_path() -> Path:
    return quant_home() / "store" / "listing_dates.parquet"


def build_listing_map_from_daily(daily: pd.DataFrame) -> dict[str, str]:
    """从行情首条记录推断上市日（回退口径，数据入库晚于真实上市时会偏保守）。"""
    if daily.empty:
        return {}
    d = daily.copy()
    if not pd.api.types.is_string_dtype(d["date"]):
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
    first = d.groupby("code")["date"].min()
    return {str(c): to_iso(str(v)) for c, v in first.items()}


def merge_jbxx_listing(mapping: dict[str, str]) -> dict[str, str]:
    """用 jbxx 缓存中的 ``上市时间`` 覆盖/补充 mapping（更接近真实 IPO 日）。"""
    out = dict(mapping)
    try:
        from quant.services.jbxx_cache import get_stock_jbxx_cache

        cache = get_stock_jbxx_cache()
        data = cache._load()
        stocks = data.get("stocks") or {}
        for code, entry in stocks.items():
            if not isinstance(entry, dict):
                continue
            raw = entry.get("上市时间") or entry.get("listing_date")
            if not raw:
                continue
            s = str(raw).strip()[:10]
            if len(s) == 8 and s.isdigit():
                s = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
            if len(s) >= 10:
                out[str(code).strip()] = s[:10]
    except Exception:
        pass
    return out


def merge_akshare_listing(mapping: dict[str, str]) -> dict[str, str]:
    """AKShare IPO 表补充/覆盖上市日。"""
    out = dict(mapping)
    try:
        from quant.data.fundamental_pit import fetch_listing_map_akshare

        for c, d in fetch_listing_map_akshare().items():
            if d:
                out[str(c).strip()] = to_iso(d)
    except Exception:
        pass
    return out


def build_listing_map(daily: pd.DataFrame | None = None) -> dict[str, str]:
    """daily 首条 → AKShare IPO → jbxx（优先级递增）。"""
    mp: dict[str, str] = {}
    if daily is not None and not daily.empty:
        mp = build_listing_map_from_daily(daily)
    mp = merge_akshare_listing(mp)
    mp = merge_jbxx_listing(mp)
    return mp


def write_listing_table(mapping: dict[str, str]) -> None:
    """写入/合并上市日表。"""
    if not mapping:
        return
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as e:
        raise ImportError("写 listing_dates 需要 pyarrow") from e
    path = listing_table_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"code": c, "listing_date": to_iso(d)} for c, d in mapping.items() if c and d]
    new_df = pd.DataFrame(rows)
    if path.is_file():
        try:
            old = pd.read_parquet(path)
            merged = pd.concat([old, new_df], ignore_index=True)
            merged = merged.drop_duplicates(subset=["code"], keep="last")
        except Exception:
            merged = new_df
    else:
        merged = new_df
    pq.write_table(pa.Table.from_pandas(merged, preserve_index=False), path)


def read_listing_map() -> dict[str, str]:
    """读取上市日表；缺失返回空 dict。"""
    path = listing_table_path()
    if not path.is_file():
        return {}
    try:
        df = pd.read_parquet(path)
        return {
            str(r["code"]): to_iso(str(r["listing_date"]))
            for _, r in df.iterrows()
            if str(r.get("code", "")).strip() and str(r.get("listing_date", "")).strip()
        }
    except Exception:
        return {}


_RESOLVED_MAP_CACHE: dict[str, str] | None = None


def resolve_listing_map(
    daily: pd.DataFrame | None = None,
    base: dict[str, str] | None = None,
    *,
    skip_network: bool = False,
) -> dict[str, str]:
    """parquet 基底 + AKShare IPO + jbxx + daily 首条（后者优先级递增）。

    进程级缓存：网络拉取（IPO + jbxx）只做一次，同进程后续复用。
    ``skip_network=True``：只读 parquet + daily 首条（跳过 IPO/jbxx 网络），
    供 update_daily 的 universe_snapshot 用（已有 listing_dates.parquet，不需拉网络）。
    """
    global _RESOLVED_MAP_CACHE
    # 跳过网络模式：parquet + daily 首条（本地，秒级）
    if skip_network:
        mp = dict(base if base is not None else read_listing_map())
        if daily is not None and not daily.empty:
            built = build_listing_map_from_daily(daily)
            mp.update(built)
        return mp
    # 正常模式（含网络）：进程级缓存
    if _RESOLVED_MAP_CACHE is not None and base is None:
        mp = dict(_RESOLVED_MAP_CACHE)
        if daily is not None and not daily.empty:
            built = build_listing_map_from_daily(daily)
            mp.update(built)
        return mp
    mp = dict(base if base is not None else read_listing_map())
    built = build_listing_map(daily)  # 含网络 IPO + jbxx
    mp.update(built)
    _RESOLVED_MAP_CACHE = dict(mp)
    return mp


def listing_days_as_of(
    code: str,
    as_of: str,
    *,
    listing_map: dict[str, str] | None = None,
    daily: pd.DataFrame | None = None,
) -> int:
    """截至 as_of 的上市交易日数（含 as_of 当日若其为交易日且有数据）。"""
    as_of = to_iso(as_of)
    mp = resolve_listing_map(daily, listing_map)
    ld = mp.get(code)
    if not ld:
        return 0
    try:
        return trading_days_between(date.fromisoformat(to_iso(ld)), date.fromisoformat(as_of))
    except Exception:
        return 0


def listing_days_map(
    daily: pd.DataFrame,
    as_of: str,
    *,
    listing_map: dict[str, str] | None = None,
) -> dict[str, int]:
    """批量：{code: listing_days}。

    优化：``resolve_listing_map`` 只调一次（旧版逐码调 = 5535 次，每次都 rebuild，
    含网络 IPO + jbxx merge → 数分钟卡死）。向量化：一次 resolve → 逐码 trading_days_between（纯计算）。
    """
    from datetime import date as _date

    as_of = to_iso(as_of)
    mp = resolve_listing_map(daily, listing_map, skip_network=True)  # 只读 parquet+daily，跳网络
    codes: set[str] = set()
    if daily is not None and not daily.empty:
        d = daily[daily["date"] <= as_of]
        codes = set(d["code"].astype(str).tolist())
    out: dict[str, int] = {}
    as_of_d = _date.fromisoformat(as_of)
    for code in codes:
        ld = mp.get(code)
        if not ld:
            out[code] = 0
            continue
        try:
            ld_d = _date.fromisoformat(to_iso(ld))
            out[code] = trading_days_between(ld_d, as_of_d)
        except Exception:
            out[code] = 0
    return out
