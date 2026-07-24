"""R2 验证：预建因子缓存 + walk-forward + 全样本回测。"""

from __future__ import annotations

import argparse
import json

from quant.backtest.engine import run_backtest
from quant.backtest.prefetch_factors import prefetch_from_archives
from quant.backtest.walk_forward import run_full_period, run_walk_forward
from quant.ml.etl import run_etl
from quant.ml.train import train_stock_rank
from quant.store.paths import quant_home


def run_validation(*, from_date: str | None = None, to_date: str | None = None) -> dict:
    prefetch_stats = prefetch_from_archives(from_date=from_date, to_date=to_date)
    etl_counts = run_etl(from_date=from_date, to_date=to_date)
    train_meta = train_stock_rank()

    rules_only = run_full_period(from_date=from_date, to_date=to_date, ml_mode="shadow")
    with_ml = run_full_period(from_date=from_date, to_date=to_date, ml_mode="gate")
    walk_shadow = run_walk_forward(from_date=from_date, to_date=to_date, n_folds=3, test_days=5, ml_mode="shadow")
    walk_gate = run_walk_forward(from_date=from_date, to_date=to_date, n_folds=3, test_days=5, ml_mode="gate")

    report = {
        "prefetch": prefetch_stats,
        "etl": etl_counts,
        "train": train_meta,
        "full_period": {"rules_shadow": rules_only, "ml_gate": with_ml},
        "walk_forward": {"shadow": walk_shadow, "gate": walk_gate},
    }
    out = quant_home() / "ml" / "validation_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="Institutional validate on historical data")
    ap.add_argument("--from", dest="from_date", default=None)
    ap.add_argument("--to", dest="to_date", default=None)
    args = ap.parse_args()
    report = run_validation(from_date=args.from_date, to_date=args.to_date)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
