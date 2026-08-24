"""反转 IC alpha 日历冻结算细调。"""

from __future__ import annotations

import argparse
import json
import pickle
import time
from datetime import date
from pathlib import Path

from quant.backtest.engine import run_backtest
from quant.backtest.metrics import compute_metrics
from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import to_iso, trading_day_list
from scripts.cli_home import add_home_argument, home_context
from scripts.research.swing_alpha_calendar import AlphaCalendarPolicy


def main() -> None:
    ap = argparse.ArgumentParser()
    add_home_argument(ap)
    ap.add_argument("--start", default="2023-08-01")
    ap.add_argument("--end", default="2025-12-31")
    args = ap.parse_args()

    with home_context(args.home):
        from quant.store.paths import quant_home

        out = Path(quant_home()) / "reports/bt_swing_band"
        cache_path = out / f"alpha_{args.start}_{args.end}.pkl"
        with open(cache_path, "rb") as f:
            alpha_by_date = pickle.load(f)

        print("load daily …", flush=True)
        daily = load_adjusted_daily()
        dates = [
            to_iso(d)
            for d in trading_day_list(date.fromisoformat(args.start), date.fromisoformat(args.end))
        ]

        jobs = []
        for topn in (10, 12, 15):
            for every in (8, 10, 12, 15, 20):
                jobs.append((f"inv_t{topn}_e{every}", topn, every, True))
        jobs.append(("inv_t20_e10", 20, 10, True))
        jobs.append(("inv_t10_e30", 10, 30, True))
        jobs.append(("raw_t10_e10", 10, 10, False))

        results = []
        for name, topn, every, invert in jobs:
            pol = AlphaCalendarPolicy(
                alpha_by_date,
                dates,
                topn=topn,
                every=every,
                invert=invert,
                max_weight=max(0.06, 0.95 / topn),
            )

            def alpha_fn(d, _rows, _abd=alpha_by_date):
                return _abd.get(d, {"__pad__": 1.0})

            t0 = time.perf_counter()
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
            row = {
                "variant": name,
                "ret": float(m["total_return_pct"]),
                "ann": float(m["ann_return_pct"]),
                "sharpe": float(m["sharpe"]),
                "mdd": float(m["max_drawdown_pct"]),
                "n_trades": int(m["n_trades"]),
                "turn_ann": float(m["turnover_annual"]),
                "avg_hold": float(m["avg_hold_days"]),
                "win_rate": float(m["win_rate"]),
                "sec_bt": round(time.perf_counter() - t0, 1),
                "topn": topn,
                "every": every,
                "invert": invert,
            }
            print(
                f"{name}: ret={row['ret']:.2f}% sharpe={row['sharpe']:.3f} mdd={row['mdd']:.1f} "
                f"turn={row['turn_ann']:.1f}",
                flush=True,
            )
            results.append(row)
            (out / f"metrics_{name}.json").write_text(json.dumps(row, indent=2), encoding="utf-8")

        results.sort(key=lambda r: -r["ret"])
        path = out / "inv_fine_summary.json"
        path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print("\n=== TOP 10 ===", flush=True)
        for r in results[:10]:
            print(
                f"  {r['variant']}: ret={r['ret']:.2f}% sharpe={r['sharpe']:.3f} mdd={r['mdd']:.1f}",
                flush=True,
            )
        print("DONE", path, flush=True)


if __name__ == "__main__":
    main()
