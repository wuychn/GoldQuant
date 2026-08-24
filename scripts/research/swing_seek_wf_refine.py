"""滚动相位精炼：空仓门控 + 更密 every，冲击月均≥10%。"""

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
from scripts.cli_home import add_home_argument, home_context
from scripts.research.swing_monthly10_explore import monthly_stats
from scripts.research.swing_seek_best import score_row
from scripts.research.swing_seek_fast import precompute_ranks


def sim_fixed(ret, ranks, dates, topn, every, offset, cost_rt=0.003):
    eq = 1.0
    curve = []
    holdings = []
    invested = 0.0
    code_i = {c: i for i, c in enumerate(ret.columns)}
    arr = ret.to_numpy(dtype=float)
    for i, dt in enumerate(dates):
        if holdings and invested > 0:
            idxs = [code_i[c] for c in holdings if c in code_i]
            if idxs:
                day = arr[i, idxs]
                day = day[np.isfinite(day)]
                if len(day):
                    eq *= 1.0 + invested * float(np.mean(day))
        curve.append((dt, eq))
        if ((i - offset) % max(1, every) == 0) or (not holdings):
            new_h = list(ranks.get(dt, [])[:topn])
            old_set, new_set = set(holdings), set(new_h)
            if old_set or new_set:
                union = max(len(old_set | new_set), 1)
                turnover = 1.0 - len(old_set & new_set) / union
                eq *= 1.0 - cost_rt * turnover * max(invested, 1.0 if new_h else 0.0)
            holdings = new_h
            invested = 1.0 if new_h else 0.0
    return curve


def walk_forward(
    ret,
    ranks,
    dates,
    topn,
    every,
    lb_months=4,
    cost_rt=0.003,
    cash_if_best_neg: bool = False,
    min_edge: float = 0.0,
):
    offset_curves = {o: sim_fixed(ret, ranks, dates, topn, every, o, cost_rt) for o in range(every)}
    eq_mat = {
        o: pd.Series({pd.Timestamp(d): e for d, e in curve}) for o, curve in offset_curves.items()
    }

    cur_o = 0
    risk_on = True
    eq = 1.0
    curve = []
    holdings = []
    invested = 0.0
    code_i = {c: i for i, c in enumerate(ret.columns)}
    arr = ret.to_numpy(dtype=float)
    date_ts = [pd.Timestamp(d) for d in dates]
    last_month = None

    for i, dt in enumerate(dates):
        ts = date_ts[i]
        mon = ts.to_period("M")
        if last_month is None:
            last_month = mon
        if mon != last_month:
            start_m = mon - lb_months
            scores = {}
            for o, s in eq_mat.items():
                mask = (s.index.to_period("M") >= start_m) & (s.index.to_period("M") < mon)
                sub = s.loc[mask]
                scores[o] = float(sub.iloc[-1] / sub.iloc[0] - 1.0) if len(sub) >= 5 else -1e9
            cur_o, best_sc = max(scores.items(), key=lambda kv: kv[1])
            if cash_if_best_neg:
                risk_on = best_sc > min_edge
            else:
                risk_on = True
            last_month = mon

        if holdings and invested > 0 and risk_on:
            idxs = [code_i[c] for c in holdings if c in code_i]
            if idxs:
                day = arr[i, idxs]
                day = day[np.isfinite(day)]
                if len(day):
                    eq *= 1.0 + invested * float(np.mean(day))
        curve.append((dt, eq))

        if not risk_on:
            if holdings:
                eq *= 1.0 - cost_rt * invested * 0.5
            holdings = []
            invested = 0.0
            continue

        if ((i - cur_o) % max(1, every) == 0) or (not holdings):
            new_h = list(ranks.get(dt, [])[:topn])
            old_set, new_set = set(holdings), set(new_h)
            if old_set or new_set:
                union = max(len(old_set | new_set), 1)
                turnover = 1.0 - len(old_set & new_set) / union
                eq *= 1.0 - cost_rt * turnover * max(invested, 1.0 if new_h else 0.0)
            holdings = new_h
            invested = 1.0 if new_h else 0.0

    return curve


def main() -> None:
    ap = argparse.ArgumentParser()
    add_home_argument(ap)
    ap.add_argument("--start", default="2023-08-01")
    ap.add_argument("--end", default="2025-12-31")
    args = ap.parse_args()
    with home_context(args.home):
        from quant.store.paths import quant_home

        out = Path(quant_home()) / "reports/bt_swing_seek"
        daily = load_adjusted_daily()
        dates = [
            to_iso(d)
            for d in trading_day_list(date.fromisoformat(args.start), date.fromisoformat(args.end))
        ]
        with open(
            Path(quant_home()) / "reports/bt_swing_band" / f"alpha_{args.start}_{args.end}.pkl",
            "rb",
        ) as f:
            alpha = pickle.load(f)
        d = daily.copy()
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
        d["code"] = d["code"].astype(str)
        d = d[d["date"].isin(dates)]
        close = d.pivot_table(index="date", columns="code", values="close", aggfunc="last").reindex(dates)
        ret = close.pct_change()
        ranks = precompute_ranks(alpha, dates, set(close.columns.astype(str)))

        results = []
        for topn in (1,):
            for every in range(14, 23):
                for lb in (3, 4, 5, 6):
                    for cash, edge in ((False, 0.0), (True, 0.0), (True, 0.05), (True, 0.10)):
                        tag = f"wf_t{topn}_e{every}_lb{lb}" + (
                            f"_cash{int(edge*100)}" if cash else ""
                        )
                        print(tag, "…", flush=True)
                        curve = walk_forward(
                            ret,
                            ranks,
                            dates,
                            topn,
                            every,
                            lb_months=lb,
                            cash_if_best_neg=cash,
                            min_edge=edge,
                        )
                        ms = monthly_stats(curve)
                        tot = (curve[-1][1] / curve[0][1] - 1) * 100
                        row = {
                            "variant": tag,
                            "topn": topn,
                            "every": every,
                            "lb": lb,
                            "cash_gate": cash,
                            "min_edge": edge,
                            "ret": round(float(tot), 2),
                            **{k: v for k, v in ms.items() if k not in ("monthly", "months_ge_10")},
                        }
                        row["score"] = round(score_row(row), 3)
                        print(
                            f"  meanM={row.get('mean_month_pct')} ge10={row.get('pct_months_ge_10')}% "
                            f"ret={row['ret']}%",
                            flush=True,
                        )
                        results.append(row)

        results.sort(key=lambda r: -float(r.get("mean_month_pct") or -999))
        path = out / "walkforward_refine.json"
        path.write_text(json.dumps(results[:40], indent=2, default=str), encoding="utf-8")
        print("\n=== BY MEAN MONTH ===", flush=True)
        for r in results[:15]:
            print(
                f"  {r['variant']}: meanM={r.get('mean_month_pct')} ge10={r.get('pct_months_ge_10')}% "
                f"ret={r['ret']}%",
                flush=True,
            )
        hit = [r for r in results if float(r.get("mean_month_pct") or 0) >= 10]
        print(f"hits>={10}: {len(hit)}", flush=True)
        if hit:
            print("HIT", hit[0]["variant"], hit[0].get("mean_month_pct"), flush=True)
        best = results[0]
        print(f"BEST_MEAN {best['variant']} {best.get('mean_month_pct')}", flush=True)
        print("DONE", path, flush=True)


if __name__ == "__main__":
    main()
