"""灵活趋势质量策略回测。"""

from __future__ import annotations

import argparse
import json
import pickle
import time
from datetime import date
from pathlib import Path

import pandas as pd

from quant.backtest.engine import run_backtest
from quant.backtest.metrics import compute_metrics
from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import to_iso, trading_day_list
from quant.swing.flex_trend import FlexTrendParams, FlexTrendPolicy
from scripts.cli_home import add_home_argument, home_context
from scripts.research.swing_monthly10_explore import monthly_stats


def main() -> None:
    ap = argparse.ArgumentParser()
    add_home_argument(ap)
    ap.add_argument("--start", default="2023-08-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--variant", default="flex_tq_v1")
    ap.add_argument("--max-stocks", type=int, default=3)
    ap.add_argument("--delta-sigma", type=float, default=0.5)
    ap.add_argument("--max-pullback", type=float, default=0.12)
    ap.add_argument("--min-eff", type=float, default=0.15)
    args = ap.parse_args()

    with home_context(args.home):
        from quant.store.paths import quant_home

        out = Path(quant_home()) / "reports/bt_flex_trend"
        out.mkdir(parents=True, exist_ok=True)

        print("load daily …", flush=True)
        daily = load_adjusted_daily()
        dates = [
            to_iso(d)
            for d in trading_day_list(
                date.fromisoformat(args.start), date.fromisoformat(args.end)
            )
        ]
        print(f"dates={len(dates)}", flush=True)

        params = FlexTrendParams(
            max_stocks=args.max_stocks,
            delta_sigma=args.delta_sigma,
            max_pullback_from_high=args.max_pullback,
            min_eff_ratio=args.min_eff,
            max_weight=min(0.45, 0.95 / max(args.max_stocks, 1)),
        )
        pol = FlexTrendPolicy(daily=daily, params=params)
        cache_path = out / f"score_cache_{args.variant}.pkl"
        t0 = time.perf_counter()
        if cache_path.exists():
            with open(cache_path, "rb") as f:
                pol.score_cache = pickle.load(f)
            d = daily.copy()
            if not pd.api.types.is_string_dtype(d["date"]):
                d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
            d["code"] = d["code"].astype(str)
            pol.daily = d
            pol._by_code = {
                str(c): g.sort_values("date").reset_index(drop=True) for c, g in d.groupby("code")
            }
            pol._prepared = True
            print(f"loaded cache {cache_path.name}", flush=True)
        else:
            print("precompute …", flush=True)
            pol.prepare(dates)
            with open(cache_path, "wb") as f:
                pickle.dump(pol.score_cache, f)
            print(f"saved {cache_path}", flush=True)
        print(f"precompute {time.perf_counter()-t0:.1f}s", flush=True)

        def alpha_fn(_d, _r):
            return {"__flex__": 1.0}

        print("run_backtest …", flush=True)
        t1 = time.perf_counter()
        broker = run_backtest(
            daily=daily,
            dates=dates,
            alpha_fn=alpha_fn,
            policy=pol,
            max_positions=args.max_stocks,
            exit_config=None,
            strict_signals=True,
            drawdown_halt=None,
        )
        m = compute_metrics(broker, daily=None)
        ms = monthly_stats(broker.equity_curve)
        row = {
            "variant": args.variant,
            "ret": float(m["total_return_pct"]),
            "ann": float(m["ann_return_pct"]),
            "sharpe": float(m["sharpe"]),
            "mdd": float(m["max_drawdown_pct"]),
            "n_trades": int(m["n_trades"]),
            "turn_ann": float(m["turnover_annual"]),
            "avg_hold": float(m["avg_hold_days"]),
            "win_rate": float(m["win_rate"]),
            "sec_bt": round(time.perf_counter() - t1, 1),
            "params": {
                "max_stocks": params.max_stocks,
                "delta_sigma": params.delta_sigma,
                "max_pullback": params.max_pullback_from_high,
                "min_eff": params.min_eff_ratio,
                "trend_fail_days": params.trend_fail_days,
            },
            **{k: v for k, v in ms.items() if k != "monthly"},
            "monthly": ms.get("monthly"),
        }
        path = out / f"metrics_{args.variant}.json"
        path.write_text(json.dumps(row, indent=2, default=str), encoding="utf-8")
        brief = {k: v for k, v in row.items() if k != "monthly"}
        print(brief, flush=True)
        print("DONE", path, flush=True)


if __name__ == "__main__":
    main()
