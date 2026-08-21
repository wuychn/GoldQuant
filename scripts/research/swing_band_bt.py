"""波段状态机回测（2023-08→2025-12）。

用法::

    python -m scripts.research.swing_band_bt --home D:/ProgramData/.quant
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date
from pathlib import Path

from quant.backtest.engine import run_backtest
from quant.backtest.metrics import compute_metrics
from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import to_iso, trading_day_list
from quant.swing.policy import SwingBandPolicy
from quant.swing.signals import SwingBandParams
from scripts.cli_home import add_home_argument, home_context


def main() -> None:
    ap = argparse.ArgumentParser(description="波段状态机回测")
    add_home_argument(ap)
    ap.add_argument("--start", default="2023-08-01")
    ap.add_argument("--end", default="2025-12-31")
    args = ap.parse_args()

    with home_context(args.home):
        from quant.store.paths import quant_home

        out = Path(quant_home()) / "reports/bt_swing_band"
        out.mkdir(parents=True, exist_ok=True)

        print("load daily …", flush=True)
        daily = load_adjusted_daily()
        dates = [
            to_iso(d)
            for d in trading_day_list(date.fromisoformat(args.start), date.fromisoformat(args.end))
        ]
        print(f"dates={len(dates)} {dates[0]}..{dates[-1]}", flush=True)

        params = SwingBandParams()  # v3 默认
        pol = SwingBandPolicy(
            daily=daily,
            params=params,
            max_stocks=5,
            full_invest=0.95,
            max_weight=0.22,
            equal_weight_new=True,
        )
        t0 = time.perf_counter()
        print("precompute buy cache …", flush=True)
        cache_path = out / "buy_cache.pkl"
        if cache_path.exists():
            import pickle

            with open(cache_path, "rb") as f:
                pol.buy_cache = pickle.load(f)
            d = daily
            if not __import__("pandas").api.types.is_string_dtype(d["date"]):
                d = d.copy()
                d["date"] = __import__("pandas").to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
            d["code"] = d["code"].astype(str)
            pol.daily = d
            pol._by_code = {
                str(c): g.sort_values("date").reset_index(drop=True) for c, g in d.groupby("code")
            }
            pol._prepared = True
            print(f"loaded buy_cache from {cache_path}", flush=True)
        else:
            pol.prepare(dates)
            import pickle

            with open(cache_path, "wb") as f:
                pickle.dump(pol.buy_cache, f)
            print(f"saved buy_cache → {cache_path}", flush=True)
        print(f"precompute {time.perf_counter()-t0:.1f}s", flush=True)

        def alpha_fn(_d, _rows):
            return {"__swing__": 1.0}  # 非空占位，避免引擎跳过交易日

        print("run_backtest …", flush=True)
        t1 = time.perf_counter()
        broker = run_backtest(
            daily=daily,
            dates=dates,
            alpha_fn=alpha_fn,
            policy=pol,
            max_positions=5,
            exit_config=None,  # 卖出完全由波段规则负责
            strict_signals=True,
            drawdown_halt=None,
        )
        m = compute_metrics(broker, daily=None)
        cost = sum(float(t.cost or 0) for t in broker.trades)
        # 卖出原因：策略侧未写入 trade.reason；用 rebalance 汇总
        row = {
            "variant": "swing_band_v3",
            "ret": m["total_return_pct"],
            "ann": m["ann_return_pct"],
            "sharpe": m["sharpe"],
            "mdd": m["max_drawdown_pct"],
            "eq": m["final_equity"],
            "n_trades": m["n_trades"],
            "turn_ann": m["turnover_annual"],
            "avg_hold": m["avg_hold_days"],
            "win_rate": m["win_rate"],
            "cost": round(cost, 2),
            "cost_pct": round(100.0 * cost / 1_000_000.0, 2),
            "sec_bt": round(time.perf_counter() - t1, 1),
            "sec_total": round(time.perf_counter() - t0, 1),
            "max_positions": 5,
        }
        (out / "metrics_v3.json").write_text(json.dumps(row, indent=2, default=str), encoding="utf-8")
        (out / "metrics.json").write_text(json.dumps(row, indent=2, default=str), encoding="utf-8")
        print(row, flush=True)
        print("DONE", out / "metrics_v3.json", flush=True)


if __name__ == "__main__":
    main()
