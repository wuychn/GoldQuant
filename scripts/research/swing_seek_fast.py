"""快速向量化寻优（预计算每日排名）→ TopK 完整回测。"""

from __future__ import annotations

import argparse
import json
import pickle
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from quant.backtest.engine import run_backtest
from quant.backtest.metrics import compute_metrics
from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import to_iso, trading_day_list
from scripts.cli_home import add_home_argument, home_context
from scripts.research.swing_alpha_calendar import AlphaCalendarPolicy
from scripts.research.swing_monthly10_explore import monthly_stats
from scripts.research.swing_seek_best import BreadthGateCalendarPolicy, build_breadth, score_row


def precompute_ranks(alpha_by_date: dict, dates: list[str], codes: set[str]) -> dict[str, list[str]]:
    """每日 invert 后从强到弱的代码列表。"""
    out: dict[str, list[str]] = {}
    for dt in dates:
        raw = alpha_by_date.get(dt) or {}
        ranked = sorted(
            ((c, -float(v)) for c, v in raw.items() if c in codes),
            key=lambda kv: -kv[1],
        )
        out[dt] = [c for c, _ in ranked]
    return out


def fast_equity(
    ret: pd.DataFrame,
    ranks: dict[str, list[str]],
    dates: list[str],
    *,
    topn: int,
    every: int,
    cost_rt: float = 0.003,
    breadth: dict[str, float] | None = None,
    breadth_max: float | None = None,
    off_scale: float = 0.0,
) -> list[tuple[str, float]]:
    """ret: date x code 日收益率；缺省 NaN。"""
    eq = 1.0
    curve: list[tuple[str, float]] = []
    holdings: list[str] = []
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

        do_reb = (i % max(1, every) == 0) or (not holdings)
        if not do_reb:
            if breadth is not None and breadth_max is not None:
                br = breadth.get(dt)
                if br is not None and br > breadth_max:
                    if off_scale <= 1e-9:
                        if holdings:
                            eq *= 1.0 - cost_rt * invested * 0.5
                        holdings = []
                        invested = 0.0
                    else:
                        invested = off_scale
                elif br is not None:
                    invested = 1.0 if holdings else 0.0
            continue

        new_h = list(ranks.get(dt, [])[:topn])
        if breadth is not None and breadth_max is not None:
            br = breadth.get(dt)
            if br is None or br > breadth_max:
                if off_scale <= 1e-9:
                    new_h = []
                    new_inv = 0.0
                else:
                    new_inv = off_scale
            else:
                new_inv = 1.0
        else:
            new_inv = 1.0 if new_h else 0.0

        old_set, new_set = set(holdings), set(new_h)
        if old_set or new_set:
            union = max(len(old_set | new_set), 1)
            turnover = 1.0 - len(old_set & new_set) / union
            eq *= 1.0 - cost_rt * turnover * max(invested, new_inv)
        holdings = new_h
        invested = new_inv if new_h else 0.0

    return curve


def main() -> None:
    ap = argparse.ArgumentParser()
    add_home_argument(ap)
    ap.add_argument("--start", default="2023-08-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--full-top", type=int, default=6)
    args = ap.parse_args()

    with home_context(args.home):
        from quant.store.paths import quant_home

        out = Path(quant_home()) / "reports/bt_swing_seek"
        out.mkdir(parents=True, exist_ok=True)
        print("load…", flush=True)
        daily = load_adjusted_daily()
        dates = [
            to_iso(d)
            for d in trading_day_list(date.fromisoformat(args.start), date.fromisoformat(args.end))
        ]
        cache = Path(quant_home()) / "reports/bt_swing_band" / f"alpha_{args.start}_{args.end}.pkl"
        with open(cache, "rb") as f:
            alpha_by_date = pickle.load(f)

        br_path = out / f"breadth_{args.start}_{args.end}.pkl"
        if br_path.exists():
            with open(br_path, "rb") as f:
                breadth = pickle.load(f)
        else:
            print("build breadth…", flush=True)
            breadth = build_breadth(daily, args.start, args.end)
            with open(br_path, "wb") as f:
                pickle.dump(breadth, f)

        print("panel…", flush=True)
        d = daily.copy()
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
        d["code"] = d["code"].astype(str)
        d = d[d["date"].isin(dates)]
        close = d.pivot_table(index="date", columns="code", values="close", aggfunc="last").reindex(dates)
        ret = close.pct_change()
        codes = set(close.columns.astype(str))
        print(f"shape={close.shape} precompute ranks…", flush=True)
        ranks = precompute_ranks(alpha_by_date, dates, codes)
        print("ranks ready", flush=True)

        jobs = []
        for topn in (1, 2, 3, 4, 5, 8, 10):
            for every in (3, 5, 8, 10, 12, 15, 20, 25):
                jobs.append(("inv", topn, every, None, None, 0.0))
        for topn in (2, 3, 5):
            for every in (5, 8, 10, 15, 20):
                for bmax in (0.32, 0.38, 0.42, 0.48):
                    for off in (0.0, 0.25):
                        jobs.append(("bg", topn, every, breadth, bmax, off))

        print(f"screen {len(jobs)}…", flush=True)
        screened = []
        t0 = time.perf_counter()
        for fam, topn, every, br, bmax, off in jobs:
            curve = fast_equity(
                ret,
                ranks,
                dates,
                topn=topn,
                every=every,
                breadth=br,
                breadth_max=bmax,
                off_scale=off,
            )
            ms = monthly_stats(curve)
            tot = (curve[-1][1] / curve[0][1] - 1.0) * 100 if curve and curve[0][1] else 0.0
            name = (
                f"inv_t{topn}_e{every}"
                if fam == "inv"
                else f"bg_t{topn}_e{every}_b{int(bmax*100)}_off{int(off*100)}"
            )
            row = {
                "variant": name,
                "family": fam,
                "topn": topn,
                "every": every,
                "breadth_max": bmax,
                "off_scale": off,
                "ret": round(float(tot), 2),
                **{k: v for k, v in ms.items() if k not in ("monthly", "months_ge_10")},
                "months_ge_10": ms.get("months_ge_10"),
            }
            row["score"] = round(score_row(row), 3)
            screened.append(row)
        print(f"screen {time.perf_counter()-t0:.1f}s", flush=True)
        screened.sort(key=lambda r: -r["score"])
        (out / "fast_screen.json").write_text(
            json.dumps(screened[:50], indent=2, default=str), encoding="utf-8"
        )
        print("\n=== FAST TOP 15 ===", flush=True)
        for r in screened[:15]:
            print(
                f"  {r['variant']}: ret={r['ret']}% meanM={r.get('mean_month_pct')} "
                f"ge10={r.get('pct_months_ge_10')}% medM={r.get('median_month_pct')} score={r['score']}",
                flush=True,
            )

        topk = screened[: args.full_top]
        must = {"inv_t3_e20", "inv_t3_e10", "inv_t5_e15", "inv_t2_e10"}
        have = {r["variant"] for r in topk}
        for m in must:
            if m not in have:
                hit = next((r for r in screened if r["variant"] == m), None)
                if hit:
                    topk.append(hit)

        print(f"\n=== FULL BT n={len(topk)} ===", flush=True)
        full = []
        for r in topk:
            name = r["variant"]
            print(f"full {name}…", flush=True)
            if r["family"] == "inv":
                pol = AlphaCalendarPolicy(
                    alpha_by_date,
                    dates,
                    topn=int(r["topn"]),
                    every=int(r["every"]),
                    invert=True,
                    max_weight=min(0.95, max(0.12, 0.95 / int(r["topn"]))),
                )
            else:
                pol = BreadthGateCalendarPolicy(
                    alpha_by_date=alpha_by_date,
                    dates=dates,
                    breadth=breadth,
                    topn=int(r["topn"]),
                    every=int(r["every"]),
                    invert=True,
                    breadth_max=float(r["breadth_max"]),
                    off_scale=float(r["off_scale"]),
                    max_weight=min(0.50, 0.95 / int(r["topn"])),
                )

            def alpha_fn(d, _rows, _abd=alpha_by_date):
                return _abd.get(d, {"__pad__": 1.0})

            t1 = time.perf_counter()
            broker = run_backtest(
                daily=daily,
                dates=dates,
                alpha_fn=alpha_fn,
                policy=pol,
                max_positions=int(r["topn"]),
                exit_config=None,
                strict_signals=True,
                drawdown_halt=None,
            )
            m = compute_metrics(broker, daily=None)
            ms = monthly_stats(broker.equity_curve)
            row = {
                "variant": name,
                "family": r["family"],
                "ret": float(m["total_return_pct"]),
                "ann": float(m["ann_return_pct"]),
                "sharpe": float(m["sharpe"]),
                "mdd": float(m["max_drawdown_pct"]),
                "n_trades": int(m["n_trades"]),
                "turn_ann": float(m["turnover_annual"]),
                "avg_hold": float(m["avg_hold_days"]),
                "sec": round(time.perf_counter() - t1, 1),
                **{k: v for k, v in ms.items() if k not in ("monthly",)},
                "fast_ret": r["ret"],
                "fast_score": r["score"],
            }
            row["score"] = round(score_row(row), 3)
            print(
                f"  → ret={row['ret']:.1f}% meanM={row.get('mean_month_pct')} "
                f"ge10={row.get('pct_months_ge_10')}% mdd={row['mdd']} score={row['score']}",
                flush=True,
            )
            full.append(row)
            (out / f"full_{name}.json").write_text(
                json.dumps(row, indent=2, default=str), encoding="utf-8"
            )

        full.sort(key=lambda x: -x["score"])
        best = full[0]
        summary = {
            "start": args.start,
            "end": args.end,
            "best": best,
            "full_rank": full,
            "fast_top15": screened[:15],
            "target_mean_month_ge_10": float(best.get("mean_month_pct") or 0) >= 10.0,
        }
        path = out / "seek_fast_summary.json"
        path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        print(
            f"\nBEST {best['variant']} meanM={best.get('mean_month_pct')} "
            f"ge10={best.get('pct_months_ge_10')}% ret={best['ret']:.1f}% "
            f"hit_target={summary['target_mean_month_ge_10']}",
            flush=True,
        )
        print("DONE", path, flush=True)


if __name__ == "__main__":
    main()
