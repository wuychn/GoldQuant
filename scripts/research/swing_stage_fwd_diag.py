"""诊断 stage 买入 cache：Top1 入场后的远期收益分布。"""

from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from quant.data.adjust import load_adjusted_daily
from scripts.cli_home import add_home_argument, home_context


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    add_home_argument(ap)
    args = ap.parse_args()
    with home_context(args.home):
        from quant.store.paths import quant_home

        daily = load_adjusted_daily()
        d = daily.copy()
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
        d["code"] = d["code"].astype(str)
        by = {c: g.sort_values("date").reset_index(drop=True) for c, g in d.groupby("code")}
        cache = pickle.load(
            open(Path(quant_home()) / "reports/bt_stage_swing/buy_cache_base.pkl", "rb")
        )
        buckets = {5: [], 10: [], 20: []}
        for dt, cands in cache.items():
            if not cands:
                continue
            code = cands[0][0]
            g = by.get(code)
            if g is None:
                continue
            idx = g.index[g["date"] == dt]
            if len(idx) == 0:
                continue
            i = int(idx[0])
            c0 = float(g.at[i, "close"])
            if c0 <= 0:
                continue
            for n, arr in buckets.items():
                j = i + n
                if j < len(g):
                    arr.append(float(g.at[j, "close"]) / c0 - 1.0)
        for n, arr in buckets.items():
            a = np.asarray(arr, dtype=float)
            print(
                f"top1 fwd{n}d n={len(a)} mean={a.mean()*100:.2f}% "
                f"p50={np.median(a)*100:.2f}% win={(a>0).mean()*100:.1f}%",
                flush=True,
            )


if __name__ == "__main__":
    main()
