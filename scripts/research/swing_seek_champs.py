"""对相位/快筛冠军做完整回测；另试 ret60 反转 alpha。"""

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
from quant.data.universe import _is_st_name
from scripts.cli_home import add_home_argument, home_context
from scripts.research.swing_alpha_calendar import AlphaCalendarPolicy
from scripts.research.swing_monthly10_explore import monthly_stats
from scripts.research.swing_seek_best import score_row


def build_ret60_alpha(daily: pd.DataFrame, dates: list[str], min_adv: float = 1.5e8) -> dict:
    d = daily.copy()
    d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
    d["code"] = d["code"].astype(str)
    if "name" not in d.columns:
        d["name"] = ""
    if "amount" not in d.columns:
        d["amount"] = 0.0
    date_set = set(dates)
    out = {dt: {} for dt in dates}
    for code, g in d.groupby("code", sort=False):
        if _is_st_name(str(g["name"].iloc[-1] if len(g) else "")):
            continue
        g = g.sort_values("date").reset_index(drop=True)
        if len(g) < 80:
            continue
        close = pd.to_numeric(g["close"], errors="coerce").to_numpy(dtype=float)
        amount = pd.to_numeric(g["amount"], errors="coerce").to_numpy(dtype=float)
        adv = pd.Series(amount).rolling(20, min_periods=10).mean().to_numpy()
        dts = g["date"].astype(str).to_numpy()
        for i in range(60, len(g)):
            dt = dts[i]
            if dt not in date_set:
                continue
            if not np.isfinite(adv[i]) or float(adv[i]) < min_adv:
                continue
            if close[i] <= 0 or close[i - 60] <= 0:
                continue
            # 分数= -ret60 → 越弱越高（invert 语义直接写入）
            out[dt][str(code)] = float(-(close[i] / close[i - 60] - 1.0))
    return out


def run_full(daily, dates, alpha, topn, every, offset, invert, name) -> dict:
    pol = AlphaCalendarPolicy(
        alpha,
        dates,
        topn=topn,
        every=every,
        invert=invert,
        offset=offset,
        max_weight=min(0.95, max(0.15, 0.95 / topn)),
    )

    def alpha_fn(d, _rows, _a=alpha):
        return _a.get(d, {"__pad__": 1.0})

    t0 = time.perf_counter()
    broker = run_backtest(
        daily=daily,
        dates=dates,
        alpha_fn=alpha_fn,
        policy=pol,
        max_positions=topn,
        exit_config=None,
        strict_signals=True,
        drawdown_halt=None,
    )
    m = compute_metrics(broker, daily=None)
    ms = monthly_stats(broker.equity_curve)
    row = {
        "variant": name,
        "topn": topn,
        "every": every,
        "offset": offset,
        "invert": invert,
        "ret": float(m["total_return_pct"]),
        "ann": float(m["ann_return_pct"]),
        "sharpe": float(m["sharpe"]),
        "mdd": float(m["max_drawdown_pct"]),
        "n_trades": int(m["n_trades"]),
        "turn_ann": float(m["turnover_annual"]),
        "avg_hold": float(m["avg_hold_days"]),
        "sec": round(time.perf_counter() - t0, 1),
        **{k: v for k, v in ms.items() if k not in ("monthly",)},
    }
    row["score"] = round(score_row(row), 3)
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    add_home_argument(ap)
    ap.add_argument("--start", default="2023-08-01")
    ap.add_argument("--end", default="2025-12-31")
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
        with open(
            Path(quant_home()) / "reports/bt_swing_band" / f"alpha_{args.start}_{args.end}.pkl",
            "rb",
        ) as f:
            ic_alpha = pickle.load(f)

        jobs = [
            # 相位冠军（快筛）
            ("ic_t1_e15_o2", ic_alpha, 1, 15, 2, True),
            ("ic_t1_e20_o1", ic_alpha, 1, 20, 1, True),
            ("ic_t1_e15_o14", ic_alpha, 1, 15, 14, True),
            ("ic_t2_e15_o2", ic_alpha, 2, 15, 2, True),
            ("ic_t2_e20_o0", ic_alpha, 2, 20, 0, True),
            ("ic_t3_e20_o0", ic_alpha, 3, 20, 0, True),
        ]

        print("build ret60 alpha…", flush=True)
        r60 = build_ret60_alpha(daily, dates)
        r60_path = out / f"ret60_alpha_{args.start}_{args.end}.pkl"
        with open(r60_path, "wb") as f:
            pickle.dump(r60, f)
        # ret60 已是弱者高分，invert=False
        for topn, every, offset in ((1, 15, 2), (1, 20, 1), (2, 20, 0), (2, 15, 2), (3, 20, 0)):
            jobs.append((f"r60_t{topn}_e{every}_o{offset}", r60, topn, every, offset, False))

        results = []
        for name, alpha, topn, every, offset, invert in jobs:
            print(f"\n=== {name} ===", flush=True)
            row = run_full(daily, dates, alpha, topn, every, offset, invert, name)
            print(
                f"{name}: ret={row['ret']:.1f}% meanM={row.get('mean_month_pct')} "
                f"ge10={row.get('pct_months_ge_10')}% med={row.get('median_month_pct')} "
                f"mdd={row['mdd']} score={row['score']}",
                flush=True,
            )
            results.append(row)
            (out / f"champ_{name}.json").write_text(
                json.dumps(row, indent=2, default=str), encoding="utf-8"
            )

        results.sort(key=lambda r: -r["score"])
        best = results[0]
        summary = {
            "start": args.start,
            "end": args.end,
            "best": best,
            "rank": results,
            "target_mean_month_ge_10": float(best.get("mean_month_pct") or 0) >= 10.0,
            "note": "相位敏感；实盘需固定 offset 规则或滚动选相位会过拟合",
        }
        path = out / "champ_summary.json"
        path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        print("\n=== RANK ===", flush=True)
        for r in results:
            print(
                f"  {r['variant']}: meanM={r.get('mean_month_pct')} ge10={r.get('pct_months_ge_10')}% "
                f"ret={r['ret']:.1f}% score={r['score']}",
                flush=True,
            )
        print(
            f"BEST={best['variant']} meanM={best.get('mean_month_pct')} "
            f"target={summary['target_mean_month_ge_10']}",
            flush=True,
        )
        print("DONE", path, flush=True)


if __name__ == "__main__":
    main()
