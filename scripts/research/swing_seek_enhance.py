"""在 WF Top1 上加：ATR 跟踪止损 + 月内回撤熔断，抬月均、砍左尾。"""

from __future__ import annotations

import argparse
import json
import pickle
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import to_iso, trading_day_list
from quant.exit.atr import atr
from scripts.cli_home import add_home_argument, home_context
from scripts.research.swing_monthly10_explore import monthly_stats
from scripts.research.swing_seek_best import score_row
from scripts.research.swing_seek_fast import precompute_ranks
from scripts.research.swing_seek_wf_refine import sim_fixed


def run_enhanced(
    close: pd.DataFrame,
    atr14: pd.DataFrame,
    ranks: dict,
    dates: list[str],
    *,
    every: int = 20,
    lb_months: int = 4,
    min_edge: float = 0.05,
    trail_atr: float = 2.5,
    month_halt_pct: float = -0.08,
    cost_rt: float = 0.003,
):
    eq_mat = {
        o: pd.Series({pd.Timestamp(d): e for d, e in sim_fixed(
            close.pct_change(), ranks, dates, 1, every, o, cost_rt
        )})
        for o in range(every)
    }
    code_i = {c: i for i, c in enumerate(close.columns)}
    px = close.to_numpy(dtype=float)
    aa = atr14.reindex(index=close.index, columns=close.columns).to_numpy(dtype=float)

    eq = 1.0
    curve = []
    holdings = []
    entry = 0.0
    peak = 0.0
    invested = 0.0
    cur_o = 0
    risk_on = True
    month_halt = False
    month_start_eq = 1.0
    last_month = None
    date_ts = [pd.Timestamp(d) for d in dates]

    for i, dt in enumerate(dates):
        ts = date_ts[i]
        mon = ts.to_period("M")
        if last_month is None:
            last_month = mon
            month_start_eq = eq
        if mon != last_month:
            start_m = mon - lb_months
            scores = {}
            for o, s in eq_mat.items():
                mask = (s.index.to_period("M") >= start_m) & (s.index.to_period("M") < mon)
                sub = s.loc[mask]
                scores[o] = float(sub.iloc[-1] / sub.iloc[0] - 1.0) if len(sub) >= 5 else -1e9
            cur_o, best = max(scores.items(), key=lambda kv: kv[1])
            risk_on = best > min_edge
            month_halt = False
            month_start_eq = eq
            last_month = mon

        # mark
        if holdings and invested > 0:
            c = holdings[0]
            j = code_i.get(c)
            if j is not None and i > 0:
                p0, p1 = px[i - 1, j], px[i, j]
                if np.isfinite(p0) and np.isfinite(p1) and p0 > 0:
                    eq *= 1.0 + invested * (float(p1) / float(p0) - 1.0)
                    peak = max(peak, float(p1))
                    # trail
                    a = aa[i, j]
                    if np.isfinite(a) and a > 0 and float(p1) <= peak - trail_atr * a:
                        eq *= 1.0 - cost_rt * invested * 0.5
                        holdings = []
                        invested = 0.0
        curve.append((dt, eq))

        # month halt
        if month_start_eq > 0 and (eq / month_start_eq - 1.0) <= month_halt_pct:
            month_halt = True
        if month_halt or not risk_on:
            if holdings:
                eq *= 1.0 - cost_rt * invested * 0.5
                holdings = []
                invested = 0.0
            continue

        if ((i - cur_o) % max(1, every) == 0) or (not holdings):
            new_h = list(ranks.get(dt, [])[:1])
            if new_h != holdings:
                if holdings or new_h:
                    eq *= 1.0 - cost_rt * 0.5 * (1.0 if holdings else 0) - cost_rt * 0.5 * (1.0 if new_h else 0)
                holdings = new_h
                invested = 1.0 if new_h else 0.0
                if new_h:
                    j = code_i.get(new_h[0])
                    entry = float(px[i, j]) if j is not None and np.isfinite(px[i, j]) else 0.0
                    peak = entry

    return curve


def main() -> None:
    ap = argparse.ArgumentParser()
    add_home_argument(ap)
    args = ap.parse_args()
    with home_context(args.home):
        from quant.store.paths import quant_home

        out = Path(quant_home()) / "reports/bt_swing_seek"
        daily = load_adjusted_daily()
        dates = [
            to_iso(d)
            for d in trading_day_list(date(2023, 8, 1), date(2025, 12, 31))
        ]
        with open(
            Path(quant_home()) / "reports/bt_swing_band/alpha_2023-08-01_2025-12-31.pkl",
            "rb",
        ) as f:
            alpha = pickle.load(f)
        d = daily.copy()
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
        d["code"] = d["code"].astype(str)
        d = d[d["date"].isin(dates)]
        close = d.pivot_table(index="date", columns="code", values="close", aggfunc="last").reindex(dates)
        # ATR per code
        print("build atr panel…", flush=True)
        atr_cols = {}
        for code, g in d.groupby("code"):
            g = g.sort_values("date")
            if len(g) < 30:
                continue
            s = atr(g, 14)
            atr_cols[str(code)] = s.groupby(g["date"].values).last()
        atr14 = pd.DataFrame(atr_cols).reindex(dates)
        ranks = precompute_ranks(alpha, dates, set(close.columns.astype(str)))

        results = []
        for trail in (1.8, 2.2, 2.5, 3.0, 99.0):
            for halt in (-0.05, -0.08, -0.12, -0.20, -1.0):
                tag = f"enh_tr{trail}_h{int(abs(halt)*100)}"
                print(tag, "…", flush=True)
                curve = run_enhanced(
                    close,
                    atr14,
                    ranks,
                    dates,
                    trail_atr=trail,
                    month_halt_pct=halt,
                )
                ms = monthly_stats(curve)
                tot = (curve[-1][1] / curve[0][1] - 1) * 100
                row = {
                    "variant": tag,
                    "trail": trail,
                    "halt": halt,
                    "ret": round(float(tot), 2),
                    **{k: v for k, v in ms.items() if k not in ("monthly", "months_ge_10")},
                }
                row["score"] = round(score_row(row), 3)
                print(
                    f"  meanM={row.get('mean_month_pct')} ge10={row.get('pct_months_ge_10')}% "
                    f"med={row.get('median_month_pct')} ret={row['ret']}% worst={row.get('worst_month_pct')}",
                    flush=True,
                )
                results.append(row)

        results.sort(key=lambda r: -float(r.get("mean_month_pct") or -999))
        path = out / "enhance_screen.json"
        path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        print("\n=== TOP BY MEAN ===", flush=True)
        for r in results[:12]:
            print(
                f"  {r['variant']}: meanM={r.get('mean_month_pct')} ge10={r.get('pct_months_ge_10')}% "
                f"ret={r['ret']}% worst={r.get('worst_month_pct')}",
                flush=True,
            )
        hit = [r for r in results if float(r.get("mean_month_pct") or 0) >= 10]
        print(f"hits>={10}: {len(hit)}", flush=True)
        print("DONE", path, flush=True)


if __name__ == "__main__":
    main()
