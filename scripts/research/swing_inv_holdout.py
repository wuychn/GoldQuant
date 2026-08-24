"""最佳反转日历参数：分段稳健性检查。"""

from __future__ import annotations

import argparse
import json
import pickle
from datetime import date
from pathlib import Path

from quant.backtest.engine import run_backtest
from quant.backtest.metrics import compute_metrics
from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import to_iso, trading_day_list
from scripts.cli_home import add_home_argument, home_context
from scripts.research.swing_alpha_calendar import AlphaCalendarPolicy


def _run(daily, alpha_by_date, dates, topn, every):
    pol = AlphaCalendarPolicy(
        alpha_by_date,
        dates,
        topn=topn,
        every=every,
        invert=True,
        max_weight=max(0.06, 0.95 / topn),
    )

    def alpha_fn(d, _rows):
        return alpha_by_date.get(d, {"__pad__": 1.0})

    broker = run_backtest(
        daily=daily,
        dates=dates,
        alpha_fn=alpha_fn,
        policy=pol,
        max_positions=topn,
        exit_config=None,
        strict_signals=True,
        drawdown_halt=None,
    )
    m = compute_metrics(broker, daily=None)
    return {
        "ret": float(m["total_return_pct"]),
        "ann": float(m["ann_return_pct"]),
        "sharpe": float(m["sharpe"]),
        "mdd": float(m["max_drawdown_pct"]),
        "n_days": len(dates),
        "n_trades": int(m["n_trades"]),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    add_home_argument(ap)
    args = ap.parse_args()
    with home_context(args.home):
        from quant.store.paths import quant_home

        out = Path(quant_home()) / "reports/bt_swing_band"
        with open(out / "alpha_2023-08-01_2025-12-31.pkl", "rb") as f:
            alpha_by_date = pickle.load(f)
        daily = load_adjusted_daily()
        windows = [
            ("full", "2023-08-01", "2025-12-31"),
            ("h1", "2023-08-01", "2024-09-30"),
            ("h2", "2024-10-01", "2025-12-31"),
            ("y2024", "2024-01-01", "2024-12-31"),
            ("y2025", "2025-01-01", "2025-12-31"),
        ]
        configs = [("t10_e20", 10, 20), ("t12_e15", 12, 15), ("t12_e20", 12, 20)]
        rows = []
        for wname, s, e in windows:
            dates = [
                to_iso(d)
                for d in trading_day_list(date.fromisoformat(s), date.fromisoformat(e))
            ]
            for cname, topn, every in configs:
                r = _run(daily, alpha_by_date, dates, topn, every)
                row = {"window": wname, "start": s, "end": e, "config": cname, **r}
                print(
                    f"{wname} {cname}: ret={r['ret']:.2f}% sharpe={r['sharpe']:.3f} mdd={r['mdd']:.1f}",
                    flush=True,
                )
                rows.append(row)
        path = out / "inv_holdout.json"
        path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print("DONE", path, flush=True)


if __name__ == "__main__":
    main()
