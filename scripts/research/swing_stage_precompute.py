"""只预计算 base cache（突破确认版）供 fwd 诊断。"""

from __future__ import annotations

import pickle
from datetime import date
from pathlib import Path

import pandas as pd

from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import to_iso, trading_day_list
from quant.swing.stage_swing import StageSwingParams, precompute_stage_buys
from scripts.cli_home import add_home_argument, home_context


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    add_home_argument(ap)
    ap.add_argument("--start", default="2023-08-01")
    ap.add_argument("--end", default="2025-12-31")
    args = ap.parse_args()
    with home_context(args.home):
        from quant.store.paths import quant_home

        out = Path(quant_home()) / "reports/bt_stage_swing"
        out.mkdir(parents=True, exist_ok=True)
        daily = load_adjusted_daily()
        d = daily.copy()
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
        d["code"] = d["code"].astype(str)
        dates = [
            to_iso(x)
            for x in trading_day_list(date.fromisoformat(args.start), date.fromisoformat(args.end))
        ]
        print("precompute base (break confirm)…", flush=True)
        cache = precompute_stage_buys(d, dates, StageSwingParams())
        path = out / "buy_cache_base.pkl"
        with open(path, "wb") as f:
            pickle.dump(cache, f)
        ns = [len(cache[dt]) for dt in dates]
        import numpy as np

        print(
            f"saved {path.name} cands/day p50={float(np.median(ns)):.0f} "
            f"mean={float(np.mean(ns)):.1f} zero={sum(1 for x in ns if x==0)}",
            flush=True,
        )


if __name__ == "__main__":
    main()
