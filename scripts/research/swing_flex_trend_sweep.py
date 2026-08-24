"""灵活趋势：降换手 / 指数过滤 / 反追高 变体扫参。"""

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
from quant.swing.flex_trend import FlexTrendParams, FlexTrendPolicy, precompute_flex_scores
from scripts.cli_home import add_home_argument, home_context
from scripts.research.swing_monthly10_explore import monthly_stats


def main() -> None:
    ap = argparse.ArgumentParser()
    add_home_argument(ap)
    ap.add_argument("--start", default="2023-08-01")
    ap.add_argument("--end", default="2025-12-31")
    args = ap.parse_args()

    with home_context(args.home):
        from quant.store.paths import quant_home

        out = Path(quant_home()) / "reports/bt_flex_trend"
        out.mkdir(parents=True, exist_ok=True)
        print("load …", flush=True)
        daily = load_adjusted_daily()
        dates = [
            to_iso(d)
            for d in trading_day_list(
                date.fromisoformat(args.start), date.fromisoformat(args.end)
            )
        ]
        dnorm = daily.copy()
        if not pd.api.types.is_string_dtype(dnorm["date"]):
            dnorm["date"] = pd.to_datetime(dnorm["date"]).dt.strftime("%Y-%m-%d")
        dnorm["code"] = dnorm["code"].astype(str)
        by_code = {
            str(c): g.sort_values("date").reset_index(drop=True) for c, g in dnorm.groupby("code")
        }

        # 买入侧：反追高版打分（新 cache）
        base_score = FlexTrendParams(max_mom_20=0.25, min_eff_ratio=0.18, max_pullback_from_high=0.10)
        cache_path = out / "score_cache_flex_v2.pkl"
        if cache_path.exists():
            with open(cache_path, "rb") as f:
                score_cache = pickle.load(f)
            print("loaded", cache_path.name, flush=True)
        else:
            print("precompute v2 …", flush=True)
            score_cache = precompute_flex_scores(dnorm, dates, base_score)
            with open(cache_path, "wb") as f:
                pickle.dump(score_cache, f)

        jobs = [
            ("noswap_tf2", dict(delta_sigma=99.0, trend_fail_days=2, regime_code="000300", max_stocks=3)),
            ("noswap_tf3", dict(delta_sigma=99.0, trend_fail_days=3, regime_code="000300", max_stocks=3)),
            ("noswap_tf4", dict(delta_sigma=99.0, trend_fail_days=4, regime_code="000300", max_stocks=3)),
            ("noswap_tf3_n5", dict(delta_sigma=99.0, trend_fail_days=3, regime_code="000300", max_stocks=5)),
            ("noswap_tf3_noreg", dict(delta_sigma=99.0, trend_fail_days=3, regime_code=None, max_stocks=3)),
            ("softswap_tf3", dict(delta_sigma=1.0, trend_fail_days=3, regime_code="000300", max_stocks=3)),
            ("softswap_tf3_d15", dict(delta_sigma=1.5, trend_fail_days=3, regime_code="000300", max_stocks=3)),
        ]

        results = []
        for name, kw in jobs:
            print(f"\n=== {name} ===", flush=True)
            params = FlexTrendParams(
                max_mom_20=0.25,
                min_eff_ratio=0.18,
                max_pullback_from_high=0.10,
                max_weight=min(0.45, 0.95 / kw["max_stocks"]),
                **kw,
            )
            pol = FlexTrendPolicy(daily=dnorm, params=params, score_cache=score_cache)
            pol._by_code = by_code
            pol._prepared = True

            def af(_d, _r):
                return {"__flex__": 1.0}

            t0 = time.perf_counter()
            broker = run_backtest(
                daily=daily,
                dates=dates,
                alpha_fn=af,
                policy=pol,
                max_positions=params.max_stocks,
                exit_config=None,
                strict_signals=True,
                drawdown_halt=None,
            )
            m = compute_metrics(broker, daily=None)
            ms = monthly_stats(broker.equity_curve)
            row = {
                "variant": name,
                "ret": float(m["total_return_pct"]),
                "sharpe": float(m["sharpe"]),
                "mdd": float(m["max_drawdown_pct"]),
                "n_trades": int(m["n_trades"]),
                "turn_ann": float(m["turnover_annual"]),
                "avg_hold": float(m["avg_hold_days"]),
                "win_rate": float(m["win_rate"]),
                "sec": round(time.perf_counter() - t0, 1),
                **{k: v for k, v in ms.items() if k not in ("monthly", "months_ge_10")},
                "months_ge_10": ms.get("months_ge_10"),
            }
            print(row, flush=True)
            results.append(row)
            (out / f"metrics_{name}.json").write_text(
                json.dumps(row, indent=2, default=str), encoding="utf-8"
            )

        results.sort(key=lambda r: -r["ret"])
        path = out / "flex_v2_summary.json"
        path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        print("\n=== TOP ===", flush=True)
        for r in results:
            print(
                f"  {r['variant']}: ret={r['ret']:.1f}% sharpe={r['sharpe']:.2f} "
                f"mdd={r['mdd']} hold={r['avg_hold']} turn={r['turn_ann']:.0f}",
                flush=True,
            )
        print("DONE", path, flush=True)


if __name__ == "__main__":
    main()
