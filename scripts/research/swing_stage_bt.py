"""阶段波段回测：回调企稳入 / 阶段转弱出 / 不及预期砍。"""

from __future__ import annotations

import argparse
import json
import pickle
import time
from dataclasses import replace
from datetime import date
from pathlib import Path

import pandas as pd

from quant.backtest.engine import run_backtest
from quant.backtest.metrics import compute_metrics
from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import to_iso, trading_day_list
from quant.swing.stage_swing import StageSwingParams, StageSwingPolicy, precompute_stage_buys
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

        out = Path(quant_home()) / "reports/bt_stage_swing"
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

        # 买入侧几套结构/回调参数 → 不同 cache
        buy_specs = {
            "base": StageSwingParams(),
            "tight_pb": StageSwingParams(
                pullback_atr_min=1.2,
                pullback_atr_max=2.2,
                pullback_sweet_atr=1.6,
                bounce_atr_min=0.4,
            ),
            "wide_pb": StageSwingParams(
                pullback_atr_min=0.8,
                pullback_atr_max=2.8,
                pullback_sweet_atr=1.4,
                bounce_atr_min=0.3,
                min_stage_rise=0.05,
            ),
        }
        caches: dict[str, dict] = {}
        for key, bp in buy_specs.items():
            path = out / f"buy_cache_{key}.pkl"
            if path.exists():
                with open(path, "rb") as f:
                    caches[key] = pickle.load(f)
                print("loaded", path.name, flush=True)
            else:
                print("precompute", key, "…", flush=True)
                caches[key] = precompute_stage_buys(dnorm, dates, bp)
                with open(path, "wb") as f:
                    pickle.dump(caches[key], f)

        jobs = []
        for buy_key in ("base", "tight_pb", "wide_pb"):
            jobs.append(
                (
                    f"{buy_key}_core",
                    buy_key,
                    dict(trail_atr_mult=2.0, fail_atr_mult=1.0, stall_hold_days=20),
                    3,
                )
            )
        jobs.append(
            (
                "base_trail28",
                "base",
                dict(trail_atr_mult=2.8, fail_atr_mult=1.2, stall_hold_days=25),
                3,
            )
        )
        jobs.append(
            (
                "base_n5",
                "base",
                dict(trail_atr_mult=2.0, fail_atr_mult=1.0, stall_hold_days=20),
                5,
            )
        )

        results = []
        for name, buy_key, sell_kw, nstock in jobs:
            print(f"\n=== {name} ===", flush=True)
            params = replace(
                buy_specs[buy_key],
                max_stocks=nstock,
                max_weight=min(0.45, 0.95 / nstock),
                **sell_kw,
            )
            pol = StageSwingPolicy(daily=dnorm, params=params, buy_cache=caches[buy_key])
            pol._by_code = by_code
            pol._prepared = True

            def af(_d, _r):
                return {"__stage__": 1.0}

            t0 = time.perf_counter()
            broker = run_backtest(
                daily=daily,
                dates=dates,
                alpha_fn=af,
                policy=pol,
                max_positions=nstock,
                exit_config=None,
                strict_signals=True,
                drawdown_halt=None,
            )
            m = compute_metrics(broker, daily=None)
            ms = monthly_stats(broker.equity_curve)
            # 卖出原因粗统计：用 last 无法跨日；从 trades reason
            reasons = {}
            for t in broker.trades:
                if str(t.side).lower() != "sell":
                    continue
                r = t.reason or "rebalance"
                reasons[r] = reasons.get(r, 0) + 1
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
                "sec": round(time.perf_counter() - t0, 1),
                "sell_reasons": reasons,
                **{k: v for k, v in ms.items() if k not in ("monthly",)},
            }
            print(
                f"{name}: ret={row['ret']:.1f}% sharpe={row['sharpe']:.2f} mdd={row['mdd']} "
                f"hold={row['avg_hold']} turn={row['turn_ann']:.0f} reasons={reasons}",
                flush=True,
            )
            results.append(row)
            (out / f"metrics_{name}.json").write_text(
                json.dumps(row, indent=2, default=str), encoding="utf-8"
            )

        results.sort(key=lambda r: -r["ret"])
        path = out / "summary.json"
        path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        print("\n=== TOP ===", flush=True)
        for r in results[:8]:
            print(f"  {r['variant']}: {r['ret']:.1f}% hold={r['avg_hold']}", flush=True)
        print("DONE", path, flush=True)


if __name__ == "__main__":
    main()
