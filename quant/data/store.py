"""parquet 离线日线库读写。

按年分区，列裁剪；pyarrow 懒导入，模块本身不依赖 pyarrow。
写路径：文件锁 + 原子替换，避免 build_daily 多线程并发写坏分区。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any, Iterator

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


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    """分区级文件锁（多线程/多进程 RMW 安全）。"""
    try:
        from filelock import FileLock
    except ImportError:
        with nullcontext():
            yield
        return
    lock = FileLock(str(path) + ".lock", timeout=120)
    with lock:
        yield


def _quarantine_corrupt(path: Path, err: BaseException) -> None:
    """损坏的 parquet 改名为 .corrupt.<ts>，避免反复读炸。"""
    ts = time.strftime("%Y%m%d%H%M%S")
    dest = path.with_name(f"{path.name}.corrupt.{ts}")
    try:
        path.replace(dest)
        print(
            f"[WARN] parquet 损坏已隔离: {path} → {dest.name} ({type(err).__name__}: {err})",
            file=sys.stderr,
        )
    except OSError as e:
        print(f"[WARN] parquet 隔离失败 {path}: {e}", file=sys.stderr)


def _read_parquet_safe(path: Path, *, empty_columns: list[str] | None = None) -> pd.DataFrame:
    """读 parquet；损坏则隔离并返回空表。"""
    if not path.is_file():
        return pd.DataFrame(columns=empty_columns or [])
    try:
        return pd.read_parquet(path)
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        corrupt_markers = (
            "thrift",
            "parquet",
            "unexpected end",
            "deserialize",
            "invalid",
            "corrupt",
            "magic bytes",
        )
        if any(m in msg for m in corrupt_markers) or isinstance(e, OSError):
            _quarantine_corrupt(path, e)
            return pd.DataFrame(columns=empty_columns or [])
        raise


def _write_parquet_atomic(path: Path, df: pd.DataFrame) -> None:
    """先写临时文件再 replace，避免写到一半被读到半截文件。"""
    import pyarrow as pa
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".parquet.tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        pq.write_table(pa.Table.from_pandas(df, preserve_index=False), tmp)
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def write_daily_raw(df: pd.DataFrame) -> None:
    """按年分区写不复权日线。追加到现有分区时先读后写去重（加锁）。"""
    _ensure_pyarrow()
    if df.empty:
        return

    d = daily_raw_dir()
    for year, g in df.groupby(df["date"].str[:4]):
        path = d / f"year={year}" / "part.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        with _file_lock(path):
            if path.is_file():
                old = _read_parquet_safe(
                    path, empty_columns=["code", "date"]
                )
                if not old.empty:
                    g = pd.concat([old, g], ignore_index=True)
                    g = g.drop_duplicates(subset=["code", "date"], keep="last")
            g = g.sort_values(["code", "date"]).reset_index(drop=True)
            _write_parquet_atomic(path, g)


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
    frames: list[pd.DataFrame] = []
    for p in sorted(root.glob("year=*/part.parquet")):
        year = p.parent.name.split("=")[1]
        if start and year < str(start)[:4]:
            continue
        if end and year > str(end)[:4]:
            continue
        part = _read_parquet_safe(p, empty_columns=["code", "date"])
        if not part.empty:
            frames.append(part)
    if not frames:
        return pd.DataFrame(columns=["code", "date"])
    df = pd.concat(frames, ignore_index=True)
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

    d = adj_factor_dir()
    for code, g in df.groupby("code"):
        path = d / f"code={code}" / "part.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        with _file_lock(path):
            if path.is_file():
                old = _read_parquet_safe(
                    path, empty_columns=["code", "date", "hfq_factor"]
                )
                if not old.empty:
                    g = pd.concat([old, g], ignore_index=True)
                    g = g.drop_duplicates(subset=["code", "date"], keep="last")
            g = g.sort_values("date").reset_index(drop=True)
            _write_parquet_atomic(path, g)


def read_adj_factor(codes: list[str] | None = None) -> pd.DataFrame:
    _ensure_pyarrow()
    root = adj_factor_dir()
    if not root.is_dir():
        return pd.DataFrame(columns=["code", "date", "hfq_factor"])
    frames: list[pd.DataFrame] = []
    for p in sorted(root.glob("code=*/part.parquet")):
        if codes:
            c = p.parent.name.split("=")[1]
            if c not in codes:
                continue
        part = _read_parquet_safe(p, empty_columns=["code", "date", "hfq_factor"])
        if not part.empty:
            frames.append(part)
    if not frames:
        return pd.DataFrame(columns=["code", "date", "hfq_factor"])
    return pd.concat(frames, ignore_index=True)


def write_index_daily(df: pd.DataFrame) -> None:
    _ensure_pyarrow()
    if df.empty:
        return

    path = index_daily_dir() / "part.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock(path):
        if path.is_file():
            old = _read_parquet_safe(path, empty_columns=["code", "date", "close"])
            if not old.empty:
                df = pd.concat([old, df], ignore_index=True)
                df = df.drop_duplicates(subset=["code", "date"], keep="last")
        df = df.sort_values(["code", "date"]).reset_index(drop=True)
        _write_parquet_atomic(path, df)


def read_index_daily(code: str = "000300", start: str | None = None, end: str | None = None) -> pd.DataFrame:
    _ensure_pyarrow()
    path = index_daily_dir() / "part.parquet"
    if not path.is_file():
        return pd.DataFrame(columns=["code", "date", "close"])
    df = _read_parquet_safe(path, empty_columns=["code", "date", "close"])
    if df.empty:
        return df
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

    d = universe_dir()
    for date_str, g in df.groupby("date"):
        year = str(date_str)[:4]
        path = d / f"year={year}" / f"{date_str}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        with _file_lock(path):
            _write_parquet_atomic(path, g)


def read_universe_snapshot(date_str: str) -> pd.DataFrame:
    _ensure_pyarrow()
    year = str(date_str)[:4]
    path = universe_dir() / f"year={year}" / f"{date_str}.parquet"
    if not path.is_file():
        return pd.DataFrame(columns=["date", "code", "name", "included"])
    return _read_parquet_safe(
        path, empty_columns=["date", "code", "name", "included"]
    )


def name_snapshot_dir() -> Path:
    return store_root() / "name_snapshot"


def write_name_snapshot(date_str: str, code_name: dict[str, str]) -> None:
    """落 PIT 名称快照 {code: name}（按日）。供 universe ST/退市过滤、涨跌停 ST
    分档使用——避免依赖 daily_raw.name（历史常缺失或为查询当下的当前名，非 PIT）。"""
    _ensure_pyarrow()
    if not code_name:
        return

    year = str(date_str)[:4]
    path = name_snapshot_dir() / f"year={year}" / f"{date_str}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(
        [{"date": date_str, "code": c, "name": n} for c, n in code_name.items()]
    )
    with _file_lock(path):
        _write_parquet_atomic(path, df)


def read_name_snapshot(date_str: str) -> dict[str, str]:
    _ensure_pyarrow()
    year = str(date_str)[:4]
    path = name_snapshot_dir() / f"year={year}" / f"{date_str}.parquet"
    if not path.is_file():
        return {}
    df = _read_parquet_safe(path, empty_columns=["date", "code", "name"])
    if df.empty:
        return {}
    return dict(zip(df["code"].astype(str), df["name"].astype(str)))


def write_calendar(trade_dates: list[str]) -> None:
    _ensure_pyarrow()
    path = calendar_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({"trade_date": sorted(set(trade_dates))})
    with _file_lock(path):
        _write_parquet_atomic(path, df)


def read_calendar() -> list[str]:
    _ensure_pyarrow()
    path = calendar_path()
    if not path.is_file():
        return []
    df = _read_parquet_safe(path, empty_columns=["trade_date"])
    if df.empty or "trade_date" not in df.columns:
        return []
    return sorted(df["trade_date"].astype(str).tolist())
