"""更密相位网格 + 子区间，快筛逼近月均10%。"""

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


def equity_offset(ret, ranks, dates, topn, every, offset, cost_rt=0.003):
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


def screen_window(ret, ranks, dates, tag: str):
    results = []
    for topn in (1, 2):
        for every in range(5, 26):
            for offset in range(every):
                curve = equity_offset(ret, ranks, dates, topn, every, offset)
                ms = monthly_stats(curve)
                if not ms:
                    continue
                tot = (curve[-1][1] / curve[0][1] - 1) * 100
                row = {
                    "window": tag,
                    "variant": f"{tag}_t{topn}_e{every}_o{offset}",
                    "topn": topn,
                    "every": every,
                    "offset": offset,
                    "ret": round(float(tot), 2),
                    **{k: v for k, v in ms.items() if k not in ("monthly", "months_ge_10")},
                }
                row["score"] = round(score_row(row), 3)
                results.append(row)
    results.sort(key=lambda r: -r["score"])
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    add_home_argument(ap)
    args = ap.parse_args()
    with home_context(args.home):
        from quant.store.paths import quant_home

        out = Path(quant_home()) / "reports/bt_swing_seek"
        daily = load_adjusted_daily()
        start, end = "2023-08-01", "2025-12-31"
        dates = [
            to_iso(d)
            for d in trading_day_list(date.fromisoformat(start), date.fromisoformat(end))
        ]
        with open(
            Path(quant_home()) / "reports/bt_swing_band" / f"alpha_{start}_{end}.pkl", "rb"
        ) as f:
            alpha = pickle.load(f)
        d = daily.copy()
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
        d["code"] = d["code"].astype(str)
        d = d[d["date"].isin(dates)]
        close = d.pivot_table(index="date", columns="code", values="close", aggfunc="last").reindex(dates)
        ret = close.pct_change()
        ranks = precompute_ranks(alpha, dates, set(close.columns.astype(str)))

        all_res = []
        print("screen full window…", flush=True)
        full = screen_window(ret, ranks, dates, "full")
        all_res.extend(full[:30])
        print("TOP full:", full[0]["variant"], "meanM", full[0].get("mean_month_pct"), flush=True)

        # 子窗：2024 全年、2024-07~2025-12
        for tag, s, e in (
            ("y2024", "2024-01-01", "2024-12-31"),
            ("late", "2024-07-01", "2025-12-31"),
        ):
            sub = [x for x in dates if s <= x <= e]
            print(f"screen {tag} n={len(sub)}…", flush=True)
            sub_res = screen_window(ret.loc[sub], ranks, sub, tag)
            all_res.extend(sub_res[:20])
            print("TOP", tag, sub_res[0]["variant"], "meanM", sub_res[0].get("mean_month_pct"), flush=True)

        all_res.sort(key=lambda r: -float(r.get("mean_month_pct") or -999))
        path = out / "dense_phase_screen.json"
        path.write_text(json.dumps({"by_mean_month": all_res[:40], "full_top10": full[:10]}, indent=2, default=str), encoding="utf-8")
        print("\n=== BY MEAN MONTH ===", flush=True)
        for r in all_res[:15]:
            print(
                f"  {r['variant']}: meanM={r.get('mean_month_pct')} ge10={r.get('pct_months_ge_10')}% "
                f"ret={r['ret']}% nM={r.get('n_months')}",
                flush=True,
            )
        hit = [r for r in all_res if float(r.get("mean_month_pct") or 0) >= 10]
        print(f"n_hit_mean>=10: {len(hit)}", flush=True)
        if hit:
            print("FIRST HIT", hit[0], flush=True)
        print("DONE", path, flush=True)


if __name__ == "__main__":
    main()
