"""从 raw 构建 ML 特征表（Parquet）。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from quant.r2.ml.features import build_market_rows, build_sector_feature_rows, build_stock_rows
from quant.store.paths import quant_home


def _list_dates(root: Path, from_d: str | None, to_d: str | None) -> list[str]:
    if not root.is_dir():
        return []
    dates = sorted(p.name for p in root.iterdir() if p.is_dir())
    if from_d:
        dates = [d for d in dates if d >= from_d]
    if to_d:
        dates = [d for d in dates if d <= to_d]
    return dates


def _load_payload(path: Path) -> dict | None:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if isinstance(obj, dict):
        inner = obj.get("data")
        return inner if isinstance(inner, dict) else obj
    return None


def run_etl(*, from_date: str | None = None, to_date: str | None = None) -> dict[str, int]:
    root = quant_home() / "daily"
    out_dir = quant_home() / "ml" / "features"
    out_dir.mkdir(parents=True, exist_ok=True)

    market_rows: list[dict] = []
    sector_rows: list[dict] = []
    stock_rows: list[dict] = []

    for d in _list_dates(root, from_date, to_date):
        day = root / d / "raw"
        if not day.is_dir():
            continue
        for name in ("evening.json", "lunch.json"):
            p = day / name
            if p.is_file():
                payload = _load_payload(p)
                if payload:
                    market_rows.extend(build_market_rows(d, payload, source=name))
                    sector_rows.extend(build_sector_feature_rows(d, payload, source=name))
                    stock_rows.extend(build_stock_rows(d, payload, source=name))
        for p in sorted(day.glob("during*.json")):
            payload = _load_payload(p)
            if payload:
                market_rows.extend(build_market_rows(d, payload, source=p.name))
                stock_rows.extend(build_stock_rows(d, payload, source=p.name))

    counts = {}
    if market_rows:
        _save_table(out_dir / "market_daily", market_rows)
        counts["market"] = len(market_rows)
    if sector_rows:
        _save_table(out_dir / "sector_daily", sector_rows)
        counts["sector"] = len(sector_rows)
    if stock_rows:
        _save_table(out_dir / "stock_daily", stock_rows)
        counts["stock"] = len(stock_rows)
    return counts


def _save_table(base: Path, rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    try:
        df.to_parquet(base.with_suffix(".parquet"), index=False)
    except Exception:
        df.to_csv(base.with_suffix(".csv"), index=False)


def main() -> None:
    ap = argparse.ArgumentParser(description="R2 ML ETL from daily/raw")
    ap.add_argument("--from", dest="from_date", default=None)
    ap.add_argument("--to", dest="to_date", default=None)
    args = ap.parse_args()
    counts = run_etl(from_date=args.from_date, to_date=args.to_date)
    print("ETL done:", counts)


if __name__ == "__main__":
    main()
