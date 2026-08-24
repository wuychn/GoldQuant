"""反转日历冻结算：买近 N 日相对弱势（A 股短线反转）。"""

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
from quant.swing.calendar_policy import CalendarBandPolicy
from quant.swing.signals import SwingBandParams
from scripts.cli_home import add_home_argument, home_context


def _invert_cache(cache: dict) -> dict:
    out = {}
    for dt, lst in cache.items():
        inv = [(c, -float(s)) for c, s in lst]
        inv.sort(key=lambda x: -x[1])
        out[dt] = inv
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    add_home_argument(ap)
    ap.add_argument("--start", default="2023-08-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument(
        "--mom-cache",
        default=None,
        help="已有 rank_mom buy_cache pkl；默认 reports/bt_swing_band/buy_cache_672c4b962d.pkl",
    )
    args = ap.parse_args()

    with home_context(args.home):
        from quant.store.paths import quant_home

        out = Path(quant_home()) / "reports/bt_swing_band"
        out.mkdir(parents=True, exist_ok=True)
        cache_path = Path(args.mom_cache) if args.mom_cache else out / "buy_cache_672c4b962d.pkl"

        print("load daily …", flush=True)
        daily = load_adjusted_daily()
        dates = [
            to_iso(d)
            for d in trading_day_list(date.fromisoformat(args.start), date.fromisoformat(args.end))
        ]
        dnorm = daily
        if not pd.api.types.is_string_dtype(dnorm["date"]):
            dnorm = dnorm.copy()
            dnorm["date"] = pd.to_datetime(dnorm["date"]).dt.strftime("%Y-%m-%d")
        dnorm = dnorm.copy()
        dnorm["code"] = dnorm["code"].astype(str)
        by_code = {str(c): g.sort_values("date").reset_index(drop=True) for c, g in dnorm.groupby("code")}

        if not cache_path.exists():
            print(f"missing {cache_path}, building rank_mom …", flush=True)
            from quant.swing.policy import SwingBandPolicy

            p0 = SwingBandParams(
                buy_mode="rank_mom",
                n_lookback=10,
                dist_high_pctile=0.0,
                require_above_ma20=True,
                require_ma_rising=False,
            )
            pol0 = SwingBandPolicy(daily=daily, params=p0)
            pol0.prepare(dates)
            with open(cache_path, "wb") as f:
                pickle.dump(pol0.buy_cache, f)
        with open(cache_path, "rb") as f:
            mom_cache = pickle.load(f)
        rev_cache = _invert_cache(mom_cache)
        print(f"cache days={len(rev_cache)} from {cache_path.name}", flush=True)

        params = SwingBandParams(buy_mode="rank_rev", n_lookback=10, require_above_ma20=True)
        jobs = [
            ("rev_top10_10d", 10, 10, 0.12),
            ("rev_top10_5d", 10, 5, 0.12),
            ("rev_top10_20d", 10, 20, 0.12),
            ("rev_top5_10d", 5, 10, 0.22),
            ("rev_top20_10d", 20, 10, 0.06),
            ("rev_top8_10d", 8, 10, 0.15),
            ("rev_top10_10d_w15", 10, 10, 0.15),
        ]
        results = []
        for name, topn, every, mw in jobs:
            print(f"\n=== {name} ===", flush=True)
            pol = CalendarBandPolicy(
                daily=dnorm,
                params=params,
                max_stocks=topn,
                full_invest=0.95,
                max_weight=mw,
                rebalance_every=every,
                buy_cache=rev_cache,
            )
            pol._by_code = by_code
            pol.bind_dates(dates)
            pol._prepared = True

            def alpha_fn(_d, _rows):
                return {"__swing__": 1.0}

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
                "eq": float(m["final_equity"]),
                "n_trades": int(m["n_trades"]),
                "turn_ann": float(m["turnover_annual"]),
                "avg_hold": float(m["avg_hold_days"]),
                "win_rate": float(m["win_rate"]),
                "sec_bt": round(time.perf_counter() - t0, 1),
                "topn": topn,
                "every": every,
            }
            print(
                f"{name}: ret={row['ret']:.2f}% sharpe={row['sharpe']:.3f} "
                f"mdd={row['mdd']:.2f} trades={row['n_trades']}",
                flush=True,
            )
            results.append(row)
            (out / f"metrics_{name}.json").write_text(
                json.dumps(row, indent=2, default=str), encoding="utf-8"
            )

        results.sort(key=lambda r: -r["ret"])
        path = out / "rev_calendar_summary.json"
        path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        print("\n=== TOP ===", flush=True)
        for r in results:
            print(f"  {r['variant']}: ret={r['ret']:.2f}% sharpe={r['sharpe']:.3f} mdd={r['mdd']}", flush=True)
        print("DONE", path, flush=True)


if __name__ == "__main__":
    main()
