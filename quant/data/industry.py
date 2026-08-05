"""行业 PIT 快照：读取与落库。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from quant.store.paths import quant_home


def _industry_dir() -> Path:
    return quant_home() / "store" / "industry"


def write_industry_snapshot(date_str: str, mapping: dict[str, str]) -> None:
    """写某日行业 PIT 快照（{code: 行业} → parquet，列 date/code/industry）。"""
    if not mapping:
        return
    d = _industry_dir()
    year = str(date_str)[:4]
    path = d / f"year={year}" / f"{date_str}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"date": date_str, "code": c, "industry": ind} for c, ind in mapping.items()]
    pd.DataFrame(rows).to_parquet(path, index=False)


def read_industry_snapshot(as_of: str) -> dict[str, str]:
    """读某日行业快照；缺失则回退到最近的前一快照（PIT，不读未来）。"""
    d = _industry_dir()
    if not d.is_dir():
        return {}
    candidates: list[Path] = []
    for p in sorted(d.rglob("*.parquet"), reverse=True):
        stem = p.stem
        if stem <= as_of:
            candidates.append(p)
            break
    if not candidates:
        return {}
    df = pd.read_parquet(candidates[0])
    if df.empty or "code" not in df.columns or "industry" not in df.columns:
        return {}
    return dict(zip(df["code"].astype(str).str.strip(), df["industry"].astype(str).str.strip()))


def fetch_current_industry_map() -> dict[str, str]:
    """拉取全市场行业映射（供 update_daily 当日落库）。

    走 ``try_with_fallback("market", "fetch_industry_map")``——所有源实现
    统一返回 ``dict[str, str]``（{code: 行业}），业务层直接用。
    全失败返回空记日志。
    """
    from quant.data.sources.interface import try_with_fallback

    try:
        result = try_with_fallback("market", "fetch_industry_map")
    except Exception as e:
        import logging

        logging.getLogger(__name__).error("行业映射全部源失败: %s", e)
        return {}
    # facade 统一返回 dict[str, str]；做一次清洗保证格式
    if isinstance(result, dict):
        return {str(k).strip(): str(v).strip() for k, v in result.items() if k and v}
    return {}
