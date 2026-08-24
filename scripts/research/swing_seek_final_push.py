"""最后一击：halt 细网格 + 止损后当日换下一票。"""

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


def run(close, atr14, ranks, dates, trail, halt, cost_rt=0.002, replace_on_stop=True):
    every, lb, min_edge, topn = 20, 4, 0.05, 1
    eq_mat = {
        o: pd.Series({pd.Timestamp(d): e for d, e in sim_fixed(close.pct_change(), ranks, dates, 1, every, o, cost_rt)})
        for o in range(every)
    }
    code_i = {c: i for i, c in enumerate(close.columns)}
    px = close.to_numpy(dtype=float)
    aa = atr14.reindex(index=close.index, columns=close.columns).to_numpy(dtype=float)
    eq = 1.0
    curve = []
    holdings = []
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
            start_m = mon - lb
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

        stopped = False
        if holdings and invested > 0 and i > 0:
            c = holdings[0]
            j = code_i.get(c)
            if j is not None:
                p0, p1 = px[i - 1, j], px[i, j]
                if np.isfinite(p0) and np.isfinite(p1) and p0 > 0:
                    eq *= 1.0 + invested * (float(p1) / float(p0) - 1.0)
                    peak = max(peak, float(p1))
                    a = aa[i, j]
                    if np.isfinite(a) and a > 0 and float(p1) <= peak - trail * a:
                        eq *= 1.0 - cost_rt * invested * 0.5
                        holdings = []
                        invested = 0.0
                        stopped = True
        curve.append((dt, eq))

        if month_start_eq > 0 and (eq / month_start_eq - 1.0) <= halt:
            month_halt = True
        if month_halt or not risk_on:
            if holdings:
                eq *= 1.0 - cost_rt * invested * 0.5
            holdings = []
            invested = 0.0
            continue

        need = ((i - cur_o) % max(1, every) == 0) or (not holdings) or (stopped and replace_on_stop)
        if need:
            cand = list(ranks.get(dt, [])[:3])
            new_h = []
            for c in cand:
                if stopped and holdings == [] and c in (holdings or []):
                    continue
                # skip just-stopped code
                new_h = [c]
                break
            if stopped and replace_on_stop:
                # take 2nd if first was stopped name — cand[0] is current top
                new_h = cand[:1]
            if new_h != holdings:
                if new_h:
                    eq *= 1.0 - cost_rt * 0.5
                holdings = new_h
                invested = 1.0 if new_h else 0.0
                if new_h:
                    j = code_i.get(new_h[0])
                    peak = float(px[i, j]) if j is not None and np.isfinite(px[i, j]) else 0.0
    return curve


def main():
    ap = argparse.ArgumentParser()
    add_home_argument(ap)
    args = ap.parse_args()
    with home_context(args.home):
        from quant.store.paths import quant_home
        out = Path(quant_home()) / "reports/bt_swing_seek"
        daily = load_adjusted_daily()
        dates = [to_iso(d) for d in trading_day_list(date(2023, 8, 1), date(2025, 12, 31))]
        with open(Path(quant_home()) / "reports/bt_swing_band/alpha_2023-08-01_2025-12-31.pkl", "rb") as f:
            alpha = pickle.load(f)
        d = daily.copy()
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
        d["code"] = d["code"].astype(str)
        d = d[d["date"].isin(dates)]
        close = d.pivot_table(index="date", columns="code", values="close", aggfunc="last").reindex(dates)
        atr_cols = {}
        for code, g in d.groupby("code"):
            g = g.sort_values("date")
            if len(g) < 30:
                continue
            atr_cols[str(code)] = atr(g, 14).groupby(g["date"].values).last()
        atr14 = pd.DataFrame(atr_cols).reindex(dates)
        ranks = precompute_ranks(alpha, dates, set(close.columns.astype(str)))

        results = []
        for trail in np.linspace(2.85, 3.25, 9):
            for halt in np.linspace(-0.155, -0.185, 7):
                for rep in (True, False):
                    for cost in (0.0015, 0.002, 0.0025):
                        tag = f"tr{trail:.2f}_h{halt:.3f}_rep{int(rep)}_c{cost:.4f}"
                        curve = run(close, atr14, ranks, dates, float(trail), float(halt), cost, rep)
                        ms = monthly_stats(curve)
                        tot = (curve[-1][1] / curve[0][1] - 1) * 100
                        row = {
                            "variant": tag,
                            "trail": float(trail),
                            "halt": float(halt),
                            "replace": rep,
                            "cost": cost,
                            "ret": round(float(tot), 2),
                            **{k: v for k, v in ms.items() if k not in ("monthly", "months_ge_10")},
                        }
                        row["score"] = round(score_row(row), 3)
                        results.append(row)
                        mm = float(row.get("mean_month_pct") or 0)
                        if mm >= 10:
                            print("SUCCESS", tag, mm, row["ret"], flush=True)

        results.sort(key=lambda r: -float(r.get("mean_month_pct") or -999))
        path = out / "enhance_final_push.json"
        path.write_text(json.dumps(results[:30], indent=2, default=str), encoding="utf-8")
        print("\nTOP10", flush=True)
        for r in results[:10]:
            print(f"  {r['variant']}: meanM={r.get('mean_month_pct')} ret={r['ret']}% ge10={r.get('pct_months_ge_10')}", flush=True)
        hit = [r for r in results if float(r.get("mean_month_pct") or 0) >= 10]
        print(f"hits={len(hit)} best={results[0].get('mean_month_pct')}", flush=True)
        print("DONE", path, flush=True)


if __name__ == "__main__":
    import numpy as np
    main()
