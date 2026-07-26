"""parquet 离线日线库读写。

按年分区，列裁剪；pyarrow 懒导入，模块本身不依赖 pyarrow。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from quant.store.paths import quant_home


def store_root() -> Path:
    return quant_home() / "store"


def daily_raw_dir() -> Path:
    return store_root() / "daily_raw"


def adj_factor_dir() -> Path:
    return store_root() / "adj_factor"


def index_daily_dir() -> Path:
    return store_root() / "index_daily"


def universe_dir() -> Path:
    return store_root() / "universe"


def calendar_path() -> Path:
    return store_root() / "calendar.parquet"


def _ensure_pyarrow():
    try:
        import pyarrow  # noqa: F401
        import pyarrow.parquet  # noqa: F401
    except ImportError as e:
        raise ImportError(
            "pyarrow 未安装，离线库读写需要它。请运行: pip install pyarrow"
        ) from e


def _year_partition_path(base: Path, date_str: str) -> Path:
    year = str(date_str)[:4]
    return base / f"year={year}" / "part.parquet"


def write_daily_raw(df: pd.DataFrame) -> None:
    """按年分区写不复权日线。追加到现有分区时先读后写去重。"""
    _ensure_pyarrow()
    if df.empty:
        return
    import pyarrow as pa
    import pyarrow.parquet as pq

    d = daily_raw_dir()
    for year, g in df.groupby(df["date"].str[:4]):
        path = d / f"year={year}" / "part.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            old = pd.read_parquet(path)
            g = pd.concat([old, g], ignore_index=True)
            g = g.drop_duplicates(subset=["code", "date"], keep="last")
        g = g.sort_values(["code", "date"]).reset_index(drop=True)
        table = pa.Table.from_pandas(g, preserve_index=False)
        pq.write_table(table, path)


def read_daily_raw(
    *,
    codes: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """读不复权日线，支持按代码/日期区间裁剪。"""
    _ensure_pyarrow()
    root = daily_raw_dir()
    if not root.is_dir():
        return pd.DataFrame(columns=["code", "date"])
    parts = []
    for p in sorted(root.glob("year=*/part.parquet")):
        year = p.parent.name.split("=")[1]
        if start and year < str(start)[:4]:
            continue
        if end and year > str(end)[:4]:
            continue
        parts.append(p)
    if not parts:
        return pd.DataFrame(columns=["code", "date"])
    df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    if start:
        df = df[df["date"] >= start]
    if end:
        df = df[df["date"] <= end]
    if codes:
        df = df[df["code"].isin(codes)]
    return df.reset_index(drop=True)


def write_adj_factor(df: pd.DataFrame) -> None:
    _ensure_pyarrow()
    if df.empty:
        return
    import pyarrow as pa
    import pyarrow.parquet as pq

    d = adj_factor_dir()
    for code, g in df.groupby("code"):
        path = d / f"code={code}" / "part.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            old = pd.read_parquet(path)
            g = pd.concat([old, g], ignore_index=True)
            g = g.drop_duplicates(subset=["code", "date"], keep="last")
        g = g.sort_values("date").reset_index(drop=True)
        pq.write_table(pa.Table.from_pandas(g, preserve_index=False), path)


def read_adj_factor(codes: list[str] | None = None) -> pd.DataFrame:
    _ensure_pyarrow()
    root = adj_factor_dir()
    if not root.is_dir():
        return pd.DataFrame(columns=["code", "date", "hfq_factor"])
    parts = []
    for p in sorted(root.glob("code=*/part.parquet")):
        if codes:
            c = p.parent.name.split("=")[1]
            if c not in codes:
                continue
        parts.append(p)
    if not parts:
        return pd.DataFrame(columns=["code", "date", "hfq_factor"])
    return pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)


def write_index_daily(df: pd.DataFrame) -> None:
    _ensure_pyarrow()
    if df.empty:
        return
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = index_daily_dir() / "part.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        old = pd.read_parquet(path)
        df = pd.concat([old, df], ignore_index=True)
        df = df.drop_duplicates(subset=["code", "date"], keep="last")
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), path)


def read_index_daily(code: str = "000300", start: str | None = None, end: str | None = None) -> pd.DataFrame:
    _ensure_pyarrow()
    path = index_daily_dir() / "part.parquet"
    if not path.is_file():
        return pd.DataFrame(columns=["code", "date", "close"])
    df = pd.read_parquet(path)
    df = df[df["code"] == code]
    if start:
        df = df[df["date"] >= start]
    if end:
        df = df[df["date"] <= end]
    return df.reset_index(drop=True)


def write_universe_snapshot(df: pd.DataFrame) -> None:
    _ensure_pyarrow()
    if df.empty:
        return
    import pyarrow as pa
    import pyarrow.parquet as pq

    d = universe_dir()
    for date_str, g in df.groupby("date"):
        year = str(date_str)[:4]
        path = d / f"year={year}" / f"{date_str}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pandas(g, preserve_index=False), path)


def read_universe_snapshot(date_str: str) -> pd.DataFrame:
    _ensure_pyarrow()
    year = str(date_str)[:4]
    path = universe_dir() / f"year={year}" / f"{date_str}.parquet"
    if not path.is_file():
        return pd.DataFrame(columns=["date", "code", "name", "included"])
    return pd.read_parquet(path)


def write_calendar(trade_dates: list[str]) -> None:
    _ensure_pyarrow()
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = calendar_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({"trade_date": sorted(set(trade_dates))})
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), path)


def read_calendar() -> list[str]:
    _ensure_pyarrow()
    path = calendar_path()
    if not path.is_file():
        return []
    df = pd.read_parquet(path)
    return sorted(df["trade_date"].astype(str).tolist())
