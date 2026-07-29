"""行业映射 PIT 落库：按日落库 code→行业，供中性化使用。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from quant.store.paths import quant_home


def industry_dir() -> Path:
    return quant_home() / "store" / "industry"


def write_industry_snapshot(as_of: str, mapping: dict[str, str]) -> None:
    """写入某日行业快照。``mapping``: {code: industry}。"""
    try:
        import pyarrow  # noqa: F401
        import pyarrow.parquet as pq
        import pyarrow as pa
    except ImportError as e:
        raise ImportError("写行业快照需要 pyarrow") from e
    if not mapping:
        return
    rows = [{"date": as_of, "code": c, "industry": ind} for c, ind in mapping.items()]
    df = pd.DataFrame(rows)
    year = str(as_of)[:4]
    path = industry_dir() / f"year={year}" / f"{as_of}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), path)


def read_industry_snapshot(as_of: str) -> dict[str, str]:
    """读某日行业快照；缺失则回退到最近的前一快照（PIT，不读未来）。"""
    root = industry_dir()
    if not root.is_dir():
        return {}
    # 精确日
    year = str(as_of)[:4]
    exact = root / f"year={year}" / f"{as_of}.parquet"
    try:
        if exact.is_file():
            df = pd.read_parquet(exact)
            return dict(zip(df["code"].astype(str), df["industry"].astype(str)))
    except Exception:
        pass
    # 回退：同目录及更早年份中 date <= as_of 的最近文件
    candidates: list[Path] = []
    for p in sorted(root.glob("year=*/????-??-??.parquet")):
        name = p.stem  # date
        if name <= as_of:
            candidates.append(p)
    if not candidates:
        return {}
    try:
        df = pd.read_parquet(candidates[-1])
        return dict(zip(df["code"].astype(str), df["industry"].astype(str)))
    except Exception:
        return {}


def fetch_current_industry_map() -> dict[str, str]:
    """拉取当前东财一级行业（供 update_daily 当日落库）。"""
    try:
        from quant.data.sources.eastmoney.industry import fetch_em_industry_board

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
