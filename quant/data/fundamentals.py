"""基本面 PIT 快照：PE/PB/ROE/营收增速（按日落库）。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from quant.data.calendar import to_iso
from quant.store.paths import quant_home


def fundamentals_dir() -> Path:
    return quant_home() / "store" / "fundamentals"


def write_fundamentals_snapshot(as_of: str, rows: list[dict]) -> None:
    """``rows``: [{code, pe_ttm, pb, roe, rev_yoy}, ...]"""
    if not rows:
        return
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as e:
        raise ImportError("写 fundamentals 快照需要 pyarrow") from e
    as_of = to_iso(as_of)
    df = pd.DataFrame(rows)
    cols = ["code", "pe_ttm", "pb", "roe", "rev_yoy"]
    for c in cols:
        if c not in df.columns:
            df[c] = None
    df = df[cols].copy()
    df.insert(0, "date", as_of)
    year = as_of[:4]
    path = fundamentals_dir() / f"year={year}" / f"{as_of}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), path)


def read_fundamentals_snapshot(as_of: str) -> dict[str, dict[str, float]]:
    """返回 {code: {pe_ttm, pb, roe, rev_yoy}}。"""
    as_of = to_iso(as_of)
    root = fundamentals_dir()
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
        out: dict[str, dict[str, float]] = {}
        for _, r in df.iterrows():
            code = str(r.get("code", "")).strip()
            if not code:
                continue
            row: dict[str, float] = {}
            for k in ("pe_ttm", "pb", "roe", "rev_yoy"):
                try:
                    v = float(r.get(k))
                    if v == v:
                        row[k] = v
                except (TypeError, ValueError):
                    pass
            if row:
                out[code] = row
        return out
    except Exception:
        return {}
