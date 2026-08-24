"""滚动选相位：每月用过去 lookback 个月最优 offset，避免偷看未来。"""

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


def month_returns(curve):
    df = pd.DataFrame(curve, columns=["date", "eq"])
    df["date"] = pd.to_datetime(df["date"])
    me = df.groupby(df["date"].dt.to_period("M")).last()
    return me["eq"].pct_change().dropna()


def walk_forward(ret, ranks, dates, topn, every, lb_months=6, cost_rt=0.003):
    """按月切换 offset：用过去 lb_months 的累计收益选最优相位。"""
    # 预计算每个 offset 的日权益路径相对收益
    offset_curves = {}
    for o in range(every):
        offset_curves[o] = sim_fixed(ret, ranks, dates, topn, every, o, cost_rt)

    # 转为每日 eq series
    eq_mat = {}
    for o, curve in offset_curves.items():
        s = pd.Series({pd.Timestamp(d): e for d, e in curve})
        eq_mat[o] = s

    # 逐日走：当前 offset，在月初评估切换
    cur_o = 0
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
            # 用过去 lb_months 选 offset
            start_m = (mon - lb_months)
            scores = {}
            for o, s in eq_mat.items():
                # 区间内收益
                mask = (s.index.to_period("M") >= start_m) & (s.index.to_period("M") < mon)
                sub = s.loc[mask]
                if len(sub) < 5:
                    scores[o] = -1e9
                else:
                    scores[o] = float(sub.iloc[-1] / sub.iloc[0] - 1.0)
            cur_o = max(scores.items(), key=lambda kv: kv[1])[0]
            last_month = mon

        if holdings and invested > 0:
            idxs = [code_i[c] for c in holdings if c in code_i]
            if idxs:
                day = arr[i, idxs]
                day = day[np.isfinite(day)]
                if len(day):
                    eq *= 1.0 + invested * float(np.mean(day))
        curve.append((dt, eq))

        if ((i - cur_o) % max(1, every) == 0) or (not holdings):
            new_h = list(ranks.get(dt, [])[:topn])
            old_set, new_set = set(holdings), set(new_h)
            if old_set or new_set:
                union = max(len(old_set | new_set), 1)
                turnover = 1.0 - len(old_set & new_set) / union
                eq *= 1.0 - cost_rt * turnover * max(invested, 1.0 if new_h else 0.0)
            holdings = new_h
            invested = 1.0 if new_h else 0.0

    return curve, cur_o


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
        for topn in (1, 2):
            for every in (10, 12, 15, 18, 20):
                for lb in (3, 4, 6):
                    print(f"WF t{topn} e{every} lb{lb}…", flush=True)
                    curve, _ = walk_forward(ret, ranks, dates, topn, every, lb_months=lb)
                    ms = monthly_stats(curve)
                    tot = (curve[-1][1] / curve[0][1] - 1) * 100
                    row = {
                        "variant": f"wf_t{topn}_e{every}_lb{lb}",
                        "topn": topn,
                        "every": every,
                        "lb_months": lb,
                        "ret": round(float(tot), 2),
                        **{k: v for k, v in ms.items() if k not in ("monthly", "months_ge_10")},
                        "months_ge_10": ms.get("months_ge_10"),
                    }
                    row["score"] = round(score_row(row), 3)
                    print(
                        f"  meanM={row.get('mean_month_pct')} ge10={row.get('pct_months_ge_10')}% "
                        f"ret={row['ret']}%",
                        flush=True,
                    )
                    results.append(row)

        results.sort(key=lambda r: -r["score"])
        path = out / "walkforward_screen.json"
        path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        print("\n=== WF TOP ===", flush=True)
        for r in results[:12]:
            print(
                f"  {r['variant']}: meanM={r.get('mean_month_pct')} ge10={r.get('pct_months_ge_10')}% "
                f"ret={r['ret']}% score={r['score']}",
                flush=True,
            )
        best = results[0]
        print(
            f"BEST {best['variant']} meanM={best.get('mean_month_pct')} "
            f"target={float(best.get('mean_month_pct') or 0) >= 10}",
            flush=True,
        )
        print("DONE", path, flush=True)


if __name__ == "__main__":
    main()
