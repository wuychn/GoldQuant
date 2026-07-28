"""非量价因子 PIT 快照：资金流 / 人气榜。

按日落库 ``store/fund_flow/year={Y}/{date}.parquet``、
``store/hot_rank/year={Y}/{date}.parquet``。回测/面板只读 ≤as_of 快照；
缺失时不回填当前 API（防前视）。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from quant.data.calendar import to_iso
from quant.store.paths import quant_home


def _snapshot_root(name: str) -> Path:
    return quant_home() / "store" / name


def write_fund_flow_snapshot(as_of: str, rows: list[dict]) -> None:
    """``rows``: [{code, flow_ratio_5}, ...]"""
    _write_snapshot("fund_flow", as_of, rows, ["code", "flow_ratio_5"])


def write_hot_rank_snapshot(as_of: str, rows: list[dict]) -> None:
    """``rows``: [{code, hot_rank_z}, ...]"""
    _write_snapshot("hot_rank", as_of, rows, ["code", "hot_rank_z"])


def write_theme_snapshot(as_of: str, rows: list[dict]) -> None:
    """``rows``: [{code, theme_mom}, ...]"""
    _write_snapshot("theme_mom", as_of, rows, ["code", "theme_mom"])


def _write_snapshot(store_name: str, as_of: str, rows: list[dict], cols: list[str]) -> None:
    if not rows:
        return
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as e:
        raise ImportError(f"写 {store_name} 快照需要 pyarrow") from e
    as_of = to_iso(as_of)
    df = pd.DataFrame(rows)
    for c in cols:
        if c not in df.columns:
            return
    df = df[cols].copy()
    df.insert(0, "date", as_of)
    year = as_of[:4]
    path = _snapshot_root(store_name) / f"year={year}" / f"{as_of}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), path)


def read_fund_flow_snapshot(as_of: str) -> dict[str, float]:
    return _read_snapshot("fund_flow", as_of, "flow_ratio_5")


def read_hot_rank_snapshot(as_of: str) -> dict[str, float]:
    return _read_snapshot("hot_rank", as_of, "hot_rank_z")


def read_theme_snapshot(as_of: str) -> dict[str, float]:
    return _read_snapshot("theme_mom", as_of, "theme_mom")


def _read_snapshot(store_name: str, as_of: str, value_col: str) -> dict[str, float]:
    as_of = to_iso(as_of)
    root = _snapshot_root(store_name)
    if not root.is_dir():
        return {}
    year = as_of[:4]
    exact = root / f"year={year}" / f"{as_of}.parquet"
    candidates: list[Path] = []
    if exact.is_file():
        candidates.append(exact)
    else:
        for p in sorted(root.glob("year=*/????-??-??.parquet")):
            if p.stem <= as_of:
                candidates.append(p)
    if not candidates:
        return {}
    try:
        df = pd.read_parquet(candidates[-1])
        out: dict[str, float] = {}
        for _, r in df.iterrows():
            code = str(r.get("code", "")).strip()
            try:
                v = float(r.get(value_col))
            except (TypeError, ValueError):
                continue
            if code and v == v:
                out[code] = v
        return out
    except Exception:
        return {}
