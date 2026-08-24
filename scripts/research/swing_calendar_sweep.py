"""日历冻结算扫参（对齐 aw_top10_10d 量级参考）。"""

from __future__ import annotations

import argparse
import hashlib
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


def _buy_key(p: SwingBandParams) -> str:
    fields = (
        p.buy_mode,
        p.n_lookback,
        p.atr_period,
        p.require_ma_rising,
        p.ma_slope_lookback,
        p.dist_high_pctile,
        p.require_above_ma20,
        p.ma_period,
        p.dist_high_lookback,
        p.momentum_atr_mult,
    )
    return hashlib.sha1(repr(fields).encode()).hexdigest()[:10]


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

        dnorm = daily
        if not pd.api.types.is_string_dtype(dnorm["date"]):
            dnorm = dnorm.copy()
            dnorm["date"] = pd.to_datetime(dnorm["date"]).dt.strftime("%Y-%m-%d")
        dnorm = dnorm.copy()
        dnorm["code"] = dnorm["code"].astype(str)
        by_code = {str(c): g.sort_values("date").reset_index(drop=True) for c, g in dnorm.groupby("code")}

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
            from quant.swing.policy import SwingBandPolicy

            pol = SwingBandPolicy(daily=daily, params=params)
            pol.prepare(dates)
            with open(path, "wb") as f:
                pickle.dump(pol.buy_cache, f)
            cache_mem[key] = pol.buy_cache
            return pol.buy_cache

        jobs = []
        # rank_mom + 日历：接近 aw_topN_Nd
        for name, n_lb, pct, topn, every, ma20, rise, mw in [
            ("cal_top10_10d", 10, 0.0, 10, 10, True, False, 0.12),
            ("cal_top10_10d_p50", 10, 50.0, 10, 10, True, False, 0.12),
            ("cal_top5_10d", 10, 0.0, 5, 10, True, False, 0.22),
            ("cal_top10_20d", 10, 0.0, 10, 20, True, False, 0.12),
            ("cal_top10_5d", 10, 0.0, 10, 5, True, False, 0.12),
            ("cal_top10_10d_n20", 20, 0.0, 10, 10, True, False, 0.12),
            ("cal_top8_10d_rise", 10, 0.0, 8, 10, True, True, 0.15),
            ("cal_top10_10d_noma", 10, 0.0, 10, 10, False, False, 0.12),
        ]:
            p = SwingBandParams(
                buy_mode="rank_mom",
                n_lookback=n_lb,
                dist_high_pctile=pct,
                require_above_ma20=ma20,
                require_ma_rising=rise,
                stall_enabled=False,
                trail_atr_mult=99.0,
                time_stop_days=999,
            )
            jobs.append((name, p, topn, every, mw, None))

        # 加沪深300 制度过滤（若库无指数代码则自动跳过）
        p_reg = SwingBandParams(
            buy_mode="rank_mom",
            n_lookback=10,
            dist_high_pctile=0.0,
            require_above_ma20=True,
            require_ma_rising=False,
        )
        jobs.append(("cal_top10_10d_hs300", p_reg, 10, 10, 0.12, "000300"))

        results = []
        for name, params, topn, every, mw, regime in jobs:
            print(f"\n=== {name} ===", flush=True)
            cache = get_cache(params)
            pol = CalendarBandPolicy(
                daily=dnorm,
                params=params,
                max_stocks=topn,
                full_invest=0.95,
                max_weight=mw,
                rebalance_every=every,
                buy_cache=cache,
                regime_code=regime,
                regime_ma=60,
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
                "max_weight": mw,
                "regime": regime,
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
        path = out / "calendar_sweep_summary.json"
        path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        print("\n=== TOP ===", flush=True)
        for r in results[:8]:
            print(f"  {r['variant']}: ret={r['ret']:.2f}% sharpe={r['sharpe']:.3f} mdd={r['mdd']}", flush=True)
        print("DONE", path, flush=True)


if __name__ == "__main__":
    main()
