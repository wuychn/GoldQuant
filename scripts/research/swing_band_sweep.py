"""波段策略同窗扫参（一次加载 daily，多配置复用/重建 buy_cache）。

用法::

    poetry run python -m scripts.research.swing_band_sweep --home D:/ProgramData/.quant
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import time
from dataclasses import replace
from datetime import date
from pathlib import Path

from quant.backtest.engine import run_backtest
from quant.backtest.metrics import compute_metrics
from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import to_iso, trading_day_list
from quant.swing.policy import SwingBandPolicy
from quant.swing.signals import SwingBandParams
from scripts.cli_home import add_home_argument, home_context


def _buy_key(p: SwingBandParams) -> str:
    fields = (
        p.buy_mode,
        p.n_lookback,
        p.atr_period,
        p.pullback_atr_mult,
        p.pullback_atr_max,
        p.bounce_days,
        p.bounce_atr_mult,
        p.max_run_atr_mult,
        p.require_ma_rising,
        p.ma_slope_lookback,
        p.dist_high_pctile,
        p.require_above_ma20,
        p.ma_period,
        p.dist_high_lookback,
        p.momentum_atr_mult,
    )
    return hashlib.sha1(repr(fields).encode()).hexdigest()[:10]


def _run_one(
    *,
    daily,
    dates,
    params: SwingBandParams,
    buy_cache: dict,
    variant: str,
    max_stocks: int,
    max_weight: float,
    equal_weight_new: bool,
) -> dict:
    pol = SwingBandPolicy(
        daily=daily,
        params=params,
        max_stocks=max_stocks,
        full_invest=0.95,
        max_weight=max_weight,
        equal_weight_new=equal_weight_new,
        buy_cache=buy_cache,
    )
    # 复用 cache 时仍需 by_code
    d = daily
    import pandas as pd

    if not pd.api.types.is_string_dtype(d["date"]):
        d = d.copy()
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
    d = d.copy()
    d["code"] = d["code"].astype(str)
    pol.daily = d
    pol._by_code = {str(c): g.sort_values("date").reset_index(drop=True) for c, g in d.groupby("code")}
    pol._prepared = True

    def alpha_fn(_d, _rows):
        return {"__swing__": 1.0}

    t0 = time.perf_counter()
    broker = run_backtest(
        daily=daily,
        dates=dates,
        alpha_fn=alpha_fn,
        policy=pol,
        max_positions=max_stocks,
        exit_config=None,
        strict_signals=True,
        drawdown_halt=None,
    )
    m = compute_metrics(broker, daily=None)
    return {
        "variant": variant,
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
        "max_stocks": max_stocks,
        "max_weight": max_weight,
        "equal_weight_new": equal_weight_new,
        "params": {
            "buy_mode": params.buy_mode,
            "n_lookback": params.n_lookback,
            "momentum_atr_mult": params.momentum_atr_mult,
            "pullback_atr_mult": params.pullback_atr_mult,
            "pullback_atr_max": params.pullback_atr_max,
            "max_run_atr_mult": params.max_run_atr_mult,
            "dist_high_pctile": params.dist_high_pctile,
            "trail_atr_mult": params.trail_atr_mult,
            "time_stop_days": params.time_stop_days,
            "require_ma_rising": params.require_ma_rising,
            "stall_enabled": params.stall_enabled,
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    add_home_argument(ap)
    ap.add_argument("--start", default="2023-08-01")
    ap.add_argument("--end", default="2025-12-31")
    args = ap.parse_args()

    with home_context(args.home):
        from quant.store.paths import quant_home

        out = Path(quant_home()) / "reports/bt_swing_band"
        out.mkdir(parents=True, exist_ok=True)

        print("load daily …", flush=True)
        daily = load_adjusted_daily()
        dates = [
            to_iso(d)
            for d in trading_day_list(date.fromisoformat(args.start), date.fromisoformat(args.end))
        ]
        print(f"dates={len(dates)}", flush=True)

        cache_mem: dict[str, dict] = {}

        def get_cache(params: SwingBandParams) -> dict:
            key = _buy_key(params)
            if key in cache_mem:
                return cache_mem[key]
            path = out / f"buy_cache_{key}.pkl"
            if path.exists():
                with open(path, "rb") as f:
                    cache_mem[key] = pickle.load(f)
                print(f"cache load {path.name}", flush=True)
                return cache_mem[key]
            print(f"cache build {path.name} …", flush=True)
            pol = SwingBandPolicy(daily=daily, params=params)
            pol.prepare(dates)
            with open(path, "wb") as f:
                pickle.dump(pol.buy_cache, f)
            cache_mem[key] = pol.buy_cache
            return pol.buy_cache

        # 配置清单：先卖侧扫（共用 v3 pullback cache），再动量买侧
        jobs: list[tuple[str, SwingBandParams, int, float, bool]] = []

        base = SwingBandParams()  # pullback v3
        # 卖侧 / 组合
        for name, trail, tstop, ms, mw, eq in [
            ("pb_trail20", 2.0, 45, 5, 0.22, True),
            ("pb_trail50", 5.0, 45, 5, 0.22, True),
            ("pb_trail50_t90", 5.0, 90, 5, 0.22, True),
            ("pb_trail50_t999", 5.0, 999, 5, 0.22, True),
            ("pb_trail35_n3", 3.5, 45, 3, 0.40, True),
            ("pb_trail50_str", 5.0, 90, 5, 0.22, False),
            ("pb_run30", 3.5, 45, 5, 0.22, True),  # buy override below
        ]:
            p = replace(base, trail_atr_mult=trail, time_stop_days=tstop)
            if name == "pb_run30":
                p = replace(p, max_run_atr_mult=3.0, trail_atr_mult=5.0, time_stop_days=90)
            jobs.append((name, p, ms, mw, eq))

        # 动量模式（设计稿）
        for name, n, a, pct, trail, tstop, ma_rise, ms, mw in [
            ("mom10_a15_p60", 10, 1.5, 60.0, 3.5, 45, False, 5, 0.22),
            ("mom10_a15_p60_t50", 10, 1.5, 60.0, 5.0, 90, False, 5, 0.22),
            ("mom10_a12_p50", 10, 1.2, 50.0, 5.0, 90, False, 5, 0.22),
            ("mom10_a15_p70_n3", 10, 1.5, 70.0, 5.0, 90, False, 3, 0.40),
            ("mom10_a15_p60_rise", 10, 1.5, 60.0, 5.0, 90, True, 5, 0.22),
            ("mom15_a20_p60", 15, 2.0, 60.0, 5.0, 90, False, 5, 0.22),
            ("mom10_noexit", 10, 1.5, 60.0, 99.0, 999, False, 5, 0.22),  # 近似无出场对照
        ]:
            p = SwingBandParams(
                buy_mode="momentum",
                n_lookback=n,
                momentum_atr_mult=a,
                dist_high_pctile=pct,
                require_above_ma20=True,
                require_ma_rising=ma_rise,
                trail_atr_mult=trail,
                time_stop_days=tstop,
                stall_enabled=False,
            )
            jobs.append((name, p, ms, mw, True))

        results = []
        for name, params, ms, mw, eq in jobs:
            print(f"\n=== {name} ===", flush=True)
            cache = get_cache(params)
            row = _run_one(
                daily=daily,
                dates=dates,
                params=params,
                buy_cache=cache,
                variant=name,
                max_stocks=ms,
                max_weight=mw,
                equal_weight_new=eq,
            )
            print(
                f"{name}: ret={row['ret']:.2f}% sharpe={row['sharpe']:.3f} "
                f"mdd={row['mdd']:.2f} trades={row['n_trades']} hold={row['avg_hold']}",
                flush=True,
            )
            results.append(row)
            (out / f"metrics_{name}.json").write_text(
                json.dumps(row, indent=2, default=str), encoding="utf-8"
            )

        results.sort(key=lambda r: -r["ret"])
        summary = out / "sweep_summary.json"
        summary.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        print("\n=== TOP ===", flush=True)
        for r in results[:8]:
            print(f"  {r['variant']}: ret={r['ret']:.2f}% sharpe={r['sharpe']:.3f} mdd={r['mdd']}", flush=True)
        print("DONE", summary, flush=True)


if __name__ == "__main__":
    main()
