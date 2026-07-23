"""R2 验证：历史回放 + ML + 分 regime 报告。"""

from __future__ import annotations

import argparse
import json

from quant.r2.backtest.engine import run_r2_backtest
from quant.r2.ml.etl import run_etl
from quant.r2.ml.train import train_stock_rank
from quant.store.paths import quant_home


def run_validation(*, from_date: str | None = None, to_date: str | None = None) -> dict:
    etl_counts = run_etl(from_date=from_date, to_date=to_date)
    train_meta = train_stock_rank()

    rules_only = run_r2_backtest(from_date=from_date, to_date=to_date, ml_mode="shadow")
    with_ml = run_r2_backtest(from_date=from_date, to_date=to_date, ml_mode="gate")

    report = {
        "etl": etl_counts,
        "train": train_meta,
        "rules_only": rules_only,
        "with_ml_gate": with_ml,
    }
    out = quant_home() / "ml" / "r2_validation_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="R2 validate on historical data")
    ap.add_argument("--from", dest="from_date", default=None)
    ap.add_argument("--to", dest="to_date", default=None)
    args = ap.parse_args()
    report = run_validation(from_date=args.from_date, to_date=args.to_date)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
