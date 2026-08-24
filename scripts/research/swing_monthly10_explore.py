"""探索「单月收益≥10%」：反转/动量日历冻结的激进仓位与换仓频率。

目标不是保证每月10%（年化不可持续），而是：
1) 现有最优策略的月收益分布
2) 更集中/更高换手变体能否提高「月≥10%」出现率与极端月收益
"""

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


def monthly_stats(equity_curve: list[tuple[str, float]]) -> dict:
    if len(equity_curve) < 2:
        return {}
    df = pd.DataFrame(equity_curve, columns=["date", "eq"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    # 每月最后一个交易日权益
    g = df.groupby(df["date"].dt.to_period("M"))
    month_end = g.last()
    rets = month_end["eq"].pct_change().dropna()
    arr = rets.to_numpy(dtype=float)
    if len(arr) == 0:
        return {}
    hit = arr >= 0.10
    return {
        "n_months": int(len(arr)),
        "mean_month_pct": round(float(arr.mean()) * 100, 2),
        "median_month_pct": round(float(np.median(arr)) * 100, 2),
        "best_month_pct": round(float(arr.max()) * 100, 2),
        "worst_month_pct": round(float(arr.min()) * 100, 2),
        "pct_months_ge_10": round(float(hit.mean()) * 100, 1),
        "n_months_ge_10": int(hit.sum()),
        "months_ge_10": [
            str(p) for p, r in zip(rets.index, arr) if r >= 0.10
        ],
        "monthly": {
            str(p): round(float(r) * 100, 2) for p, r in zip(rets.index, arr)
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    add_home_argument(ap)
    ap.add_argument("--start", default="2023-08-01")
    ap.add_argument("--end", default="2025-12-31")
    args = ap.parse_args()

    with home_context(args.home):
        from quant.store.paths import quant_home

        out = Path(quant_home()) / "reports/bt_monthly10"
        out.mkdir(parents=True, exist_ok=True)
        cache = (
            Path(quant_home())
            / "reports/bt_swing_band"
            / f"alpha_{args.start}_{args.end}.pkl"
        )
        if not cache.exists():
            raise SystemExit(f"缺少 alpha 缓存: {cache}")

        print("load …", flush=True)
        with open(cache, "rb") as f:
            abd = pickle.load(f)
        daily = load_adjusted_daily()
        dates = [
            to_iso(d)
            for d in trading_day_list(
                date.fromisoformat(args.start), date.fromisoformat(args.end)
            )
        ]
        print(f"dates={len(dates)} alpha_days={len(abd)}", flush=True)

        # 激进网格：少票 + 快换 + 反转为主；附带动量对照看右尾
        jobs = [
            ("inv_t10_e20", 10, 20, True),  # 已知总收益最优对照
            ("inv_t5_e10", 5, 10, True),
            ("inv_t5_e5", 5, 5, True),
            ("inv_t3_e10", 3, 10, True),
            ("inv_t3_e5", 3, 5, True),
            ("inv_t3_e3", 3, 3, True),
            ("inv_t2_e5", 2, 5, True),
            ("inv_t1_e5", 1, 5, True),
            ("inv_t1_e3", 1, 3, True),
            ("mom_t3_e5", 3, 5, False),
            ("mom_t1_e5", 1, 5, False),
            ("inv_t5_e20", 5, 20, True),
            ("inv_t3_e20", 3, 20, True),
        ]

        results = []
        for name, topn, every, invert in jobs:
            print(f"\n=== {name} ===", flush=True)
            t0 = time.perf_counter()
            pol = AlphaCalendarPolicy(
                abd,
                dates,
                topn=topn,
                every=every,
                invert=invert,
                max_weight=min(0.95, max(0.08, 0.95 / topn)),
                full_invest=0.95,
            )

            def af(d, _r, _a=abd):
                return _a.get(d, {"__pad__": 1.0})

            broker = run_backtest(
                daily=daily,
                dates=dates,
                alpha_fn=af,
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
                "invert": invert,
                "ret": float(m["total_return_pct"]),
                "ann": float(m["ann_return_pct"]),
                "sharpe": float(m["sharpe"]),
                "mdd": float(m["max_drawdown_pct"]),
                "n_trades": int(m["n_trades"]),
                "turn_ann": float(m["turnover_annual"]),
                "sec_bt": round(time.perf_counter() - t0, 1),
                **ms,
            }
            # 打印摘要时去掉大字典
            brief = {k: v for k, v in row.items() if k not in ("monthly", "months_ge_10")}
            print(brief, flush=True)
            results.append(row)
            (out / f"metrics_{name}.json").write_text(
                json.dumps(row, indent=2, default=str), encoding="utf-8"
            )

        # 排序：先看月≥10%出现率，再看最佳月、总收益
        results.sort(
            key=lambda r: (
                -r.get("pct_months_ge_10", 0),
                -r.get("best_month_pct", -999),
                -r.get("ret", -999),
            )
        )
        path = out / "summary.json"
        path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        print("\n=== TOP by months≥10% ===", flush=True)
        for r in results[:8]:
            print(
                f"  {r['variant']}: months≥10%={r.get('pct_months_ge_10')}% "
                f"({r.get('n_months_ge_10')}/{r.get('n_months')}) "
                f"best_m={r.get('best_month_pct')}% mean_m={r.get('mean_month_pct')}% "
                f"total={r['ret']:.1f}% mdd={r['mdd']}",
                flush=True,
            )
        print("DONE", path, flush=True)


if __name__ == "__main__":
    main()
