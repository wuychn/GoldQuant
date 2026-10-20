"""涨停池 PIT 快照：东财 ``stock_zt_pool_em(date)`` 可按交易日回填。

人气榜没有历史日期参数，不在本模块。封板时间/炸板/连板以源站当日截面为准。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from quant.data.calendar import to_iso
from quant.store.paths import quant_home

ZT_POOL_COLUMNS = (
    "code",
    "date",
    "name",
    "pct",
    "close",
    "amount",
    "float_mv",
    "total_mv",
    "turnover",
    "seal_amt",
    "first_seal",
    "last_seal",
    "break_n",
    "zt_stat",
    "boards",
    "industry",
)

_RENAME = {
    "代码": "code",
    "名称": "name",
    "涨跌幅": "pct",
    "最新价": "close",
    "成交额": "amount",
    "流通市值": "float_mv",
    "总市值": "total_mv",
    "换手率": "turnover",
    "封板资金": "seal_amt",
    "首次封板时间": "first_seal",
    "最后封板时间": "last_seal",
    "炸板次数": "break_n",
    "涨停统计": "zt_stat",
    "连板数": "boards",
    "所属行业": "industry",
}


def _store() -> Path:
    return quant_home() / "store" / "zt_pool"


def _code6(s) -> str:
    t = str(s or "").strip()
    if t.endswith((".SZ", ".SH", ".BJ")):
        t = t.split(".")[0]
    digits = "".join(ch for ch in t if ch.isdigit())
    return digits.zfill(6) if digits else ""


def snapshot_path(as_of: str) -> Path:
    day = to_iso(as_of)
    return _store() / f"year={day[:4]}" / f"{day}.parquet"


def normalize_zt_pool(df: pd.DataFrame | None, *, as_of: str) -> pd.DataFrame:
    empty = pd.DataFrame(columns=list(ZT_POOL_COLUMNS))
    day = to_iso(as_of)
    if df is None or df.empty:
        return empty
    d = df.copy().rename(columns=_RENAME)
    if "code" not in d.columns:
        return empty
    d["code"] = d["code"].map(_code6)
    d["date"] = day
    if "name" in d.columns:
        d["name"] = d["name"].astype(str)
    else:
        d["name"] = ""
    for c in ZT_POOL_COLUMNS:
        if c not in d.columns:
            d[c] = pd.NA
    d = d[list(ZT_POOL_COLUMNS)].copy()
    d = d[d["code"].astype(str).str.len() == 6]
    for c in ("pct", "close", "amount", "float_mv", "total_mv", "turnover", "seal_amt", "break_n", "boards"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    return d.reset_index(drop=True)


def write_zt_pool_snapshot(as_of: str, df: pd.DataFrame) -> int:
    day = to_iso(as_of)
    d = df if df is not None else pd.DataFrame(columns=list(ZT_POOL_COLUMNS))
    if list(d.columns) != list(ZT_POOL_COLUMNS):
        d = normalize_zt_pool(d, as_of=day)
    path = snapshot_path(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    d.to_parquet(path, index=False)
    return int(len(d))


def zt_pool_exists(as_of: str) -> bool:
    return snapshot_path(as_of).is_file()


def read_zt_pool(as_of: str) -> pd.DataFrame:
    path = snapshot_path(as_of)
    if not path.is_file():
        return pd.DataFrame(columns=list(ZT_POOL_COLUMNS))
    try:
        df = pd.read_parquet(path)
    except Exception:
        return pd.DataFrame(columns=list(ZT_POOL_COLUMNS))
    return normalize_zt_pool(df, as_of=as_of) if not df.empty else df


def read_zt_pool_range(start: str, end: str) -> pd.DataFrame:
    start, end = to_iso(start), to_iso(end)
    root = _store()
    if not root.is_dir():
        return pd.DataFrame(columns=list(ZT_POOL_COLUMNS))
    frames: list[pd.DataFrame] = []
    for p in sorted(root.glob("year=*/*.parquet")):
        stem = p.stem
        if start <= stem <= end:
            try:
                frames.append(pd.read_parquet(p))
            except Exception:
                continue
    if not frames:
        return pd.DataFrame(columns=list(ZT_POOL_COLUMNS))
    return pd.concat(frames, ignore_index=True)


def capture_zt_pool(as_of: str) -> int:
    from quant.data.sources.interface import try_with_fallback

    as_of = to_iso(as_of)
    df = try_with_fallback("market", "fetch_zt_pool_hist", date=as_of)
    return write_zt_pool_snapshot(as_of, df)


def zt_rows_from_daily_frame(df: pd.DataFrame) -> pd.DataFrame:
    """用不复权日线重建涨停截面（公开东财涨停池接口只有当天）。

    连板数 = 截至当日连续收盘涨停天数。无封板时间/炸板/封板资金。
    """
    empty = pd.DataFrame(columns=list(ZT_POOL_COLUMNS))
    if df is None or df.empty:
        return empty
    d = df.copy()
    d["code"] = d["code"].map(_code6)
    d["date"] = pd.to_datetime(d["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    d = d[d["code"].astype(str).str.len() == 6]
    d = d.sort_values(["code", "date"]).reset_index(drop=True)
    if "pre_close" not in d.columns:
        d["pre_close"] = pd.NA
    if "name" not in d.columns:
        d["name"] = ""
    d["pre_close"] = pd.to_numeric(d["pre_close"], errors="coerce")
    d["close"] = pd.to_numeric(d["close"], errors="coerce")
    miss = d["pre_close"].isna() | (d["pre_close"] <= 0)
    d.loc[miss, "pre_close"] = pd.to_numeric(
        d.groupby("code", sort=False)["close"].shift(1).loc[miss], errors="coerce"
    )
    code = d["code"].astype(str)
    name_u = d["name"].fillna("").astype(str).str.upper()
    limit = np.where(code.str.startswith(("8", "4", "92")) & ~code.str.startswith("688"), 0.30, 0.10)
    limit = np.where(code.str.startswith(("300", "688")), 0.20, limit)
    st = name_u.str.startswith("ST") | name_u.str.startswith("*ST") | d["name"].fillna("").astype(str).str.contains("退", regex=False)
    limit = np.where(st & ~code.str.startswith(("300", "688", "8", "4", "92")), 0.05, limit)
    up = (d["pre_close"] * (1.0 + limit)).round(2)
    is_up = (d["close"] >= up - 0.005) & (d["pre_close"] > 0) & (d["close"] > 0)
    reset = (~is_up).groupby(d["code"]).cumsum()
    boards = is_up.groupby([d["code"], reset]).cumsum()
    hit = d.loc[is_up].copy()
    hit["boards"] = boards.loc[is_up].astype(int)
    hit["pct"] = (hit["close"] / hit["pre_close"] - 1.0) * 100.0
    hit["zt_stat"] = hit["boards"].astype(str) + "/" + hit["boards"].astype(str)
    if "turnover_rate" in hit.columns:
        hit["turnover"] = pd.to_numeric(hit["turnover_rate"], errors="coerce")
    else:
        hit["turnover"] = pd.NA
    for c in ("seal_amt", "first_seal", "last_seal", "break_n"):
        hit[c] = pd.NA
    hit["industry"] = ""
    for c in ("amount", "float_mv", "total_mv"):
        if c in hit.columns:
            hit[c] = pd.to_numeric(hit[c], errors="coerce")
        else:
            hit[c] = pd.NA
    return hit[list(ZT_POOL_COLUMNS)].reset_index(drop=True)


def reconstruct_zt_pool_range(*, start: str, end: str, skip_existing: bool = True) -> dict[str, int]:
    """从 ``daily_raw`` 重建 ``start~end`` 涨停池快照。"""
    from datetime import date, timedelta

    from quant.data.store import read_daily_raw

    start, end = to_iso(start), to_iso(end)
    look = (date.fromisoformat(start) - timedelta(days=40)).isoformat()
    daily = read_daily_raw(start=look, end=end)
    all_rows = zt_rows_from_daily_frame(daily)
    n_days = n_rows = n_skip = 0
    if all_rows is None:
        all_rows = pd.DataFrame(columns=list(ZT_POOL_COLUMNS))
    if not all_rows.empty:
        all_rows = all_rows[(all_rows["date"] >= start) & (all_rows["date"] <= end)]
    from quant.data.calendar import trading_day_list
    from datetime import date as date_cls

    trading = [to_iso(d) for d in trading_day_list(date_cls.fromisoformat(start), date_cls.fromisoformat(end))]
    have = set(all_rows["date"].astype(str)) if not all_rows.empty else set()
    for day in trading:
        if skip_existing and zt_pool_exists(day):
            n_skip += 1
            continue
        part = all_rows[all_rows["date"] == day] if day in have else pd.DataFrame(columns=list(ZT_POOL_COLUMNS))
        write_zt_pool_snapshot(day, part)
        n_days += 1
        n_rows += int(len(part))
    return {"days": n_days, "rows": n_rows, "skip": n_skip}
