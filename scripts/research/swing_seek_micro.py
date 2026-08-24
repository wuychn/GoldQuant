"""微扰冲击 10%：halt∈[-0.14,-0.19]，trail∈[2.7,3.3]，熔断后半仓。"""

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


def run(
    close,
    atr14,
    ranks,
    dates,
    *,
    every=20,
    lb=4,
    trail=3.0,
    halt=-0.18,
    halt_scale=0.0,
    min_edge=0.05,
    cost_rt=0.003,
    topn=1,
):
    eq_mat = {
        o: pd.Series(
            {pd.Timestamp(d): e for d, e in sim_fixed(close.pct_change(), ranks, dates, topn, every, o, cost_rt)}
        )
        for o in range(every)
    }
    code_i = {c: i for i, c in enumerate(close.columns)}
    px = close.to_numpy(dtype=float)
    aa = atr14.reindex(index=close.index, columns=close.columns).to_numpy(dtype=float)
    eq = 1.0
    curve = []
    holdings = []
    peak = {}
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
            scores = {
                o: (
                    float(sub.iloc[-1] / sub.iloc[0] - 1.0)
                    if len(sub := s.loc[(s.index.to_period("M") >= start_m) & (s.index.to_period("M") < mon)])
                    >= 5
                    else -1e9
                )
                for o, s in eq_mat.items()
            }
            cur_o, best = max(scores.items(), key=lambda kv: kv[1])
            risk_on = best > min_edge
            month_halt = False
            month_start_eq = eq
            last_month = mon

        target_inv = halt_scale if month_halt else (1.0 if risk_on else 0.0)

        if holdings and invested > 0:
            rs = []
            for c in holdings:
                j = code_i.get(c)
                if j is None or i == 0:
                    continue
                p0, p1 = px[i - 1, j], px[i, j]
                if np.isfinite(p0) and np.isfinite(p1) and p0 > 0:
                    rs.append(float(p1) / float(p0) - 1.0)
                    peak[c] = max(peak.get(c, float(p1)), float(p1))
                    a = aa[i, j]
                    if np.isfinite(a) and a > 0 and float(p1) <= peak[c] - trail * a:
                        # stop that name
                        pass  # mark for drop
            if rs:
                eq *= 1.0 + invested * float(np.mean(rs))
            # drop stopped
            keep = []
            for c in holdings:
                j = code_i.get(c)
                if j is None:
                    continue
                p1 = px[i, j]
                a = aa[i, j]
                if np.isfinite(a) and a > 0 and np.isfinite(p1) and float(p1) <= peak.get(c, p1) - trail * a:
                    continue
                keep.append(c)
            if len(keep) < len(holdings):
                eq *= 1.0 - cost_rt * invested * 0.5 * (1 - len(keep) / max(len(holdings), 1))
                holdings = keep
                if not holdings:
                    invested = 0.0

        curve.append((dt, eq))

        if month_start_eq > 0 and (eq / month_start_eq - 1.0) <= halt:
            month_halt = True
            target_inv = halt_scale

        if not risk_on:
            target_inv = 0.0

        if target_inv <= 1e-9:
            if holdings:
                eq *= 1.0 - cost_rt * invested * 0.5
            holdings = []
            invested = 0.0
            continue

        do_reb = ((i - cur_o) % max(1, every) == 0) or (not holdings)
        if do_reb:
            new_h = list(ranks.get(dt, [])[:topn])
            if set(new_h) != set(holdings) or abs(invested - target_inv) > 1e-6:
                eq *= 1.0 - cost_rt * 0.5
                holdings = new_h
                invested = target_inv if new_h else 0.0
                peak = {}
                for c in holdings:
                    j = code_i.get(c)
                    if j is not None and np.isfinite(px[i, j]):
                        peak[c] = float(px[i, j])
        else:
            invested = target_inv if holdings else 0.0

    return curve


def main() -> None:
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
        for trail in (2.7, 2.9, 3.0, 3.1, 3.3):
            for halt in (-0.14, -0.16, -0.17, -0.18, -0.19):
                for hs in (0.0, 0.25, 0.40):
                    for edge in (0.03, 0.05, 0.08):
                        for cost in (0.002, 0.003):
                            tag = f"tr{trail}_h{int(abs(halt)*100)}_hs{int(hs*100)}_e{int(edge*100)}_c{int(cost*1000)}"
                            curve = run(
                                close,
                                atr14,
                                ranks,
                                dates,
                                trail=trail,
                                halt=halt,
                                halt_scale=hs,
                                min_edge=edge,
                                cost_rt=cost,
                            )
                            ms = monthly_stats(curve)
                            tot = (curve[-1][1] / curve[0][1] - 1) * 100
                            row = {
                                "variant": tag,
                                "trail": trail,
                                "halt": halt,
                                "halt_scale": hs,
                                "min_edge": edge,
                                "cost": cost,
                                "ret": round(float(tot), 2),
                                **{k: v for k, v in ms.items() if k not in ("monthly", "months_ge_10")},
                            }
                            row["score"] = round(score_row(row), 3)
                            results.append(row)
                            mm = float(row.get("mean_month_pct") or 0)
                            if mm >= 10:
                                print("SUCCESS", tag, "meanM", mm, "ret", row["ret"], flush=True)
                            elif mm >= 9.7:
                                print("NEAR", tag, "meanM", mm, flush=True)

        results.sort(key=lambda r: -float(r.get("mean_month_pct") or -999))
        path = out / "enhance_micro.json"
        path.write_text(json.dumps(results[:40], indent=2, default=str), encoding="utf-8")
        print("\n=== TOP 12 ===", flush=True)
        for r in results[:12]:
            print(
                f"  {r['variant']}: meanM={r.get('mean_month_pct')} ge10={r.get('pct_months_ge_10')}% "
                f"ret={r['ret']}% worst={r.get('worst_month_pct')}",
                flush=True,
            )
        hit = [r for r in results if float(r.get("mean_month_pct") or 0) >= 10]
        print(f"hits>=10: {len(hit)}", flush=True)
        print("DONE", path, flush=True)


if __name__ == "__main__":
    main()
