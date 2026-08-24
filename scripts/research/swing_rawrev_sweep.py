"""短窗反转日历：近 N 日收益最低者买入，每 K 日换仓（无均线过滤）。"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from quant.backtest.engine import run_backtest
from quant.backtest.metrics import compute_metrics
from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import to_iso, trading_day_list
from quant.swing.calendar_policy import CalendarBandPolicy
from quant.swing.signals import SwingBandParams
from scripts.cli_home import add_home_argument, home_context


def build_ret_rank_cache(
    daily: pd.DataFrame,
    dates: list[str],
    *,
    n_lookback: int = 10,
    invert: bool = True,
    min_adv: float = 0.0,
) -> dict[str, list[tuple[str, float]]]:
    d = daily.copy()
    if not pd.api.types.is_string_dtype(d["date"]):
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
    d["code"] = d["code"].astype(str)
    date_set = set(dates)
    feats: dict[str, list[tuple[str, float]]] = {dt: [] for dt in dates}
    groups = list(d.groupby("code", sort=False))
    n_codes = len(groups)
    for gi, (code, g) in enumerate(groups):
        if (gi + 1) % 500 == 0:
            print(f"  ret-rank {gi+1}/{n_codes}", flush=True)
        g = g.sort_values("date")
        if len(g) < n_lookback + 2:
            continue
        dates_c = g["date"].astype(str).to_numpy()
        close = pd.to_numeric(g["close"], errors="coerce").to_numpy(dtype=float)
        amt = (
            pd.to_numeric(g["amount"], errors="coerce").to_numpy(dtype=float)
            if "amount" in g.columns
            else np.zeros(len(g))
        )
        for i in range(n_lookback, len(g)):
            dt = dates_c[i]
            if dt not in date_set:
                continue
            a0, a1 = close[i - n_lookback], close[i]
            if not np.isfinite(a0) or not np.isfinite(a1) or a0 <= 0:
                continue
            if min_adv > 0:
                adv = float(np.nanmean(amt[max(0, i - 19) : i + 1]))
                if not np.isfinite(adv) or adv < min_adv:
                    continue
            ret = a1 / a0 - 1.0
            score = -ret if invert else ret
            feats[dt].append((str(code), float(score)))
    out = {}
    for dt in dates:
        lst = feats.get(dt) or []
        lst.sort(key=lambda x: -x[1])
        out[dt] = lst
    print(f"  ret-rank done days={len(dates)}", flush=True)
    return out


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
        dnorm = daily.copy()
        if not pd.api.types.is_string_dtype(dnorm["date"]):
            dnorm["date"] = pd.to_datetime(dnorm["date"]).dt.strftime("%Y-%m-%d")
        dnorm["code"] = dnorm["code"].astype(str)
        by_code = {str(c): g.sort_values("date").reset_index(drop=True) for c, g in dnorm.groupby("code")}

        caches = {}
        for key, n, inv, adv in [
            ("rev10", 10, True, 0.0),
            ("rev5", 5, True, 0.0),
            ("rev20", 20, True, 0.0),
            ("rev10_liq", 10, True, 1e8),  # ADV≥1亿
            ("mom10", 10, False, 0.0),
        ]:
            print(f"build cache {key} …", flush=True)
            caches[key] = build_ret_rank_cache(
                daily, dates, n_lookback=n, invert=inv, min_adv=adv
            )

        jobs = [
            ("rawrev_top10_10d", "rev10", 10, 10, 0.12),
            ("rawrev_top10_5d", "rev10", 10, 5, 0.12),
            ("rawrev_top10_20d", "rev10", 10, 20, 0.12),
            ("rawrev_top5_10d", "rev10", 5, 10, 0.22),
            ("rawrev_top20_10d", "rev10", 20, 10, 0.06),
            ("rawrev5_top10_10d", "rev5", 10, 10, 0.12),
            ("rawrev20_top10_10d", "rev20", 10, 10, 0.12),
            ("rawrev_liq_top10_10d", "rev10_liq", 10, 10, 0.12),
            ("rawmom_top10_10d", "mom10", 10, 10, 0.12),
        ]
        results = []
        params = SwingBandParams(buy_mode="rank_rev")
        for name, ck, topn, every, mw in jobs:
            print(f"\n=== {name} ===", flush=True)
            pol = CalendarBandPolicy(
                daily=dnorm,
                params=params,
                max_stocks=topn,
                full_invest=0.95,
                max_weight=mw,
                rebalance_every=every,
                buy_cache=caches[ck],
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
                "n_trades": int(m["n_trades"]),
                "avg_hold": float(m["avg_hold_days"]),
                "sec_bt": round(time.perf_counter() - t0, 1),
            }
            print(f"{name}: ret={row['ret']:.2f}% sharpe={row['sharpe']:.3f} mdd={row['mdd']:.2f}", flush=True)
            results.append(row)
            (out / f"metrics_{name}.json").write_text(json.dumps(row, indent=2), encoding="utf-8")

        results.sort(key=lambda r: -r["ret"])
        path = out / "rawrev_summary.json"
        path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print("\n=== TOP ===", flush=True)
        for r in results:
            print(f"  {r['variant']}: {r['ret']:.2f}%", flush=True)
        print("DONE", path, flush=True)


if __name__ == "__main__":
    main()
