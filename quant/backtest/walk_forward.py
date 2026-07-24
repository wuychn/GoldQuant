"""Walk-forward 回测验证（机构标准：分折 + 样本外）。"""

from __future__ import annotations

import json
from typing import Any

from quant.backtest.engine import run_backtest
from quant.backtest.metrics import compute_metrics
from quant.store.paths import quant_home


def _list_dates(from_d: str | None, to_d: str | None) -> list[str]:
    root = quant_home() / "daily"
    if not root.is_dir():
        return []
    dates = sorted(p.name for p in root.iterdir() if p.is_dir())
    if from_d:
        dates = [d for d in dates if d >= from_d]
    if to_d:
        dates = [d for d in dates if d <= to_d]
    return dates


def _has_evening(d: str) -> bool:
    return (quant_home() / "daily" / d / "raw" / "evening.json").is_file()


def run_walk_forward(
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    n_folds: int = 3,
    test_days: int = 5,
    ml_mode: str = "shadow",
) -> dict[str, Any]:
    """滚动样本外：每折 test_days 个交易日，汇总 metrics。"""
    dates = [d for d in _list_dates(from_date, to_date) if _has_evening(d)]
    if len(dates) < test_days + 2:
        return {"ok": False, "note": f"交易日不足: {len(dates)}", "folds": []}

    step = max(test_days, (len(dates) - test_days) // max(n_folds, 1))
    folds: list[dict] = []

    start = 0
    fold_i = 0
    while start + test_days <= len(dates) and fold_i < n_folds:
        test_slice = dates[start : start + test_days]
        train_end = test_slice[0]
        test_from, test_to = test_slice[0], test_slice[-1]

        metrics = run_backtest(
            from_date=test_from,
            to_date=test_to,
            ml_mode=ml_mode,
        )
        folds.append(
            {
                "fold": fold_i + 1,
                "train_before": train_end,
                "test_from": test_from,
                "test_to": test_to,
                "test_days": len(test_slice),
                "metrics": metrics,
            }
        )
        start += step
        fold_i += 1

    def _avg(key: str) -> float:
        vals = [f["metrics"].get(key, 0) for f in folds if isinstance(f.get("metrics"), dict)]
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    summary = {
        "ok": True,
        "n_folds": len(folds),
        "ml_mode": ml_mode,
        "avg_total_return": _avg("total_return"),
        "avg_win_rate": _avg("win_rate"),
        "avg_max_drawdown": _avg("max_drawdown"),
        "avg_realized_pnl": _avg("realized_pnl"),
        "avg_trade_count": _avg("trade_count"),
    }
    return {"summary": summary, "folds": folds}


def run_full_period(
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    ml_mode: str = "shadow",
) -> dict[str, Any]:
    return run_backtest(from_date=from_date, to_date=to_date, ml_mode=ml_mode)
