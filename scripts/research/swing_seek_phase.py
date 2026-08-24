"""日历相位扫：every=N 的起点 offset 0..N-1，找月均最高相位。"""

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
from scripts.research.swing_seek_fast import fast_equity, precompute_ranks


class PhaseCalendar:
    """与 AlphaCalendarPolicy 相同，但 i % every == offset。"""

    def __init__(self, ranks, dates, topn, every, offset):
        self.ranks = ranks
        self.dates = dates
        self.topn = topn
        self.every = every
        self.offset = offset


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
            alpha_by_date = pickle.load(f)

        d = daily.copy()
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
        d["code"] = d["code"].astype(str)
        d = d[d["date"].isin(dates)]
        close = d.pivot_table(index="date", columns="code", values="close", aggfunc="last").reindex(dates)
        ret = close.pct_change()
        ranks = precompute_ranks(alpha_by_date, dates, set(close.columns.astype(str)))

        # 修改 fast_equity 支持 offset：内联复制小改
        def equity_offset(topn, every, offset):
            eq = 1.0
            curve = []
            holdings = []
            invested = 0.0
            code_i = {c: i for i, c in enumerate(ret.columns)}
            arr = ret.to_numpy(dtype=float)
            cost_rt = 0.003
            for i, dt in enumerate(dates):
                if holdings and invested > 0:
                    idxs = [code_i[c] for c in holdings if c in code_i]
                    if idxs:
                        day = arr[i, idxs]
                        day = day[np.isfinite(day)]
                        if len(day):
                            eq *= 1.0 + invested * float(np.mean(day))
                curve.append((dt, eq))
                do_reb = ((i - offset) % max(1, every) == 0) or (not holdings)
                if not do_reb:
                    continue
                new_h = list(ranks.get(dt, [])[:topn])
                old_set, new_set = set(holdings), set(new_h)
                if old_set or new_set:
                    union = max(len(old_set | new_set), 1)
                    turnover = 1.0 - len(old_set & new_set) / union
                    eq *= 1.0 - cost_rt * turnover * max(invested, 1.0)
                holdings = new_h
                invested = 1.0 if new_h else 0.0
            return curve

        results = []
        for topn, every in ((2, 20), (2, 15), (3, 20), (3, 15), (1, 20), (2, 25), (1, 15)):
            for offset in range(every):
                curve = equity_offset(topn, every, offset)
                ms = monthly_stats(curve)
                tot = (curve[-1][1] / curve[0][1] - 1) * 100
                row = {
                    "variant": f"inv_t{topn}_e{every}_o{offset}",
                    "topn": topn,
                    "every": every,
                    "offset": offset,
                    "ret": round(float(tot), 2),
                    **{k: v for k, v in ms.items() if k not in ("monthly", "months_ge_10")},
                }
                row["score"] = round(score_row(row), 3)
                results.append(row)

        results.sort(key=lambda r: -r["score"])
        path = out / "phase_screen.json"
        path.write_text(json.dumps(results[:40], indent=2, default=str), encoding="utf-8")
        print("=== PHASE TOP 20 ===", flush=True)
        for r in results[:20]:
            print(
                f"  {r['variant']}: ret={r['ret']}% meanM={r.get('mean_month_pct')} "
                f"ge10={r.get('pct_months_ge_10')}% med={r.get('median_month_pct')} score={r['score']}",
                flush=True,
            )
        best = results[0]
        print(
            f"BEST {best['variant']} meanM={best.get('mean_month_pct')} "
            f"ge10={best.get('pct_months_ge_10')} target={float(best.get('mean_month_pct') or 0)>=10}",
            flush=True,
        )
        print("DONE", path, flush=True)


if __name__ == "__main__":
    main()
