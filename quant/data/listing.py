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
        from app.services.stock_jbxx_cache import get_stock_jbxx_cache

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


def resolve_listing_map(
    daily: pd.DataFrame | None = None,
    base: dict[str, str] | None = None,
) -> dict[str, str]:
    """parquet 基底 + AKShare IPO + jbxx + daily 首条（后者优先级递增）。"""
    mp = dict(base if base is not None else read_listing_map())
    built = build_listing_map(daily)
    return {**mp, **built}


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
    """批量：{code: listing_days}。"""
    as_of = to_iso(as_of)
    mp = resolve_listing_map(daily, listing_map)
    codes = set()
    if daily is not None and not daily.empty:
        d = daily[daily["date"] <= as_of]
        codes = set(d["code"].astype(str).tolist())
    out: dict[str, int] = {}
    for code in codes:
        out[code] = listing_days_as_of(code, as_of, listing_map=mp, daily=daily)
    return out
