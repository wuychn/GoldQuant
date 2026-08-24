"""IC alpha + 日历冻结算（对齐 aw_topN_Nd 参考）。"""

from __future__ import annotations

import argparse
import json
import pickle
import time
from datetime import date
from pathlib import Path

from quant.backtest.engine import run_backtest
from quant.backtest.metrics import compute_metrics
from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import to_iso, trading_day_list
from quant.factors.alpha_builder import build_alpha_by_date
from scripts.cli_home import add_home_argument, home_context


class AlphaCalendarPolicy:
    def __init__(
        self,
        alpha_by_date: dict[str, dict[str, float]],
        dates: list[str],
        *,
        topn: int = 10,
        every: int = 10,
        full_invest: float = 0.95,
        max_weight: float = 0.12,
        invert: bool = False,
        offset: int = 0,
    ):
        self.alpha_by_date = alpha_by_date
        self.dates = list(dates)
        self.date_i = {d: i for i, d in enumerate(self.dates)}
        self.topn = topn
        self.every = every
        self.full_invest = full_invest
        self.max_weight = max_weight
        self.invert = invert
        self.offset = int(offset) % max(1, int(every))
        self._frozen: dict[str, float] = {}
        self.holding_snapshots: dict = {}

    @property
    def n(self) -> int:
        return self.topn

    def target_weights(self, alpha, prices, current, date):
        as_of = str(date)[:10]
        i = self.date_i.get(as_of)
        if i is None:
            return {c: w for c, w in self._frozen.items() if c in (prices or {})}
        do_reb = ((i - self.offset) % max(1, self.every) == 0) or (not self._frozen)
        if do_reb:
            raw = dict(self.alpha_by_date.get(as_of) or alpha or {})
            if self.invert:
                raw = {c: -float(v) for c, v in raw.items()}
            ranked = sorted(raw.items(), key=lambda kv: -kv[1])
            picked = [c for c, _ in ranked if c in (prices or {})][: self.topn]
            if not picked:
                self._frozen = {}
                return {}
            w = min(self.full_invest / len(picked), self.max_weight)
            self._frozen = {c: w for c in picked}
        return {c: w for c, w in self._frozen.items() if c in (prices or {}) and w > 1e-6}


def main() -> None:
    ap = argparse.ArgumentParser()
    add_home_argument(ap)
    ap.add_argument("--start", default="2023-08-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--topn", type=int, default=10)
    ap.add_argument("--every", type=int, default=20)
    ap.add_argument("--invert", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--variant", default=None)
    args = ap.parse_args()

    with home_context(args.home):
        from quant.config import load_quant_config
        from quant.store.paths import quant_home

        yml_ac = ((load_quant_config().get("swing_band") or {}).get("alpha_calendar") or {})
        if args.variant is None:
            # CLI 未显式扫参时，允许 yml 覆盖默认
            if "topn" in yml_ac:
                args.topn = int(yml_ac["topn"])
            if "rebalance_every" in yml_ac:
                args.every = int(yml_ac["rebalance_every"])
            if "invert" in yml_ac and args.invert is True:
                args.invert = bool(yml_ac["invert"])

        out = Path(quant_home()) / "reports/bt_swing_band"
        out.mkdir(parents=True, exist_ok=True)
        cache_path = out / f"alpha_{args.start}_{args.end}.pkl"

        print("load daily …", flush=True)
        daily = load_adjusted_daily()
        dates = [
            to_iso(d)
            for d in trading_day_list(date.fromisoformat(args.start), date.fromisoformat(args.end))
        ]
        print(f"dates={len(dates)}", flush=True)

        if cache_path.exists():
            print(f"load alpha cache {cache_path}", flush=True)
            with open(cache_path, "rb") as f:
                alpha_by_date = pickle.load(f)
        else:
            print(f"build alpha workers={args.workers} …", flush=True)
            t0 = time.perf_counter()
            alpha_by_date = build_alpha_by_date(dates, daily, workers=args.workers)
            with open(cache_path, "wb") as f:
                pickle.dump(alpha_by_date, f)
            print(f"alpha done {time.perf_counter()-t0:.1f}s → {cache_path}", flush=True)

        jobs = []
        if args.variant:
            jobs.append((args.variant, args.topn, args.every, args.invert))
        else:
            # 默认跑 yml/推荐口径（反转 + top10 / 20 日冻结）
            tag = "inv" if args.invert else "raw"
            jobs = [(f"aw_top{args.topn}_{args.every}d_{tag}", args.topn, args.every, args.invert)]

        results = []
        for name, topn, every, invert in jobs:
            print(f"\n=== {name} ===", flush=True)
            pol = AlphaCalendarPolicy(
                alpha_by_date,
                dates,
                topn=topn,
                every=every,
                invert=invert,
                max_weight=max(0.08, 0.95 / topn),
            )

            def alpha_fn(d, _rows, _abd=alpha_by_date):
                return _abd.get(d, {"__pad__": 1.0})

            t1 = time.perf_counter()
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
            row = {
                "variant": name,
                "ret": float(m["total_return_pct"]),
                "ann": float(m["ann_return_pct"]),
                "sharpe": float(m["sharpe"]),
                "mdd": float(m["max_drawdown_pct"]),
                "eq": float(m["final_equity"]),
                "n_trades": int(m["n_trades"]),
                "turn_ann": float(m["turnover_annual"]),
                "avg_hold": float(m["avg_hold_days"]),
                "win_rate": float(m["win_rate"]),
                "sec_bt": round(time.perf_counter() - t1, 1),
                "topn": topn,
                "every": every,
                "invert": invert,
            }
            print(
                f"{name}: ret={row['ret']:.2f}% sharpe={row['sharpe']:.3f} mdd={row['mdd']:.2f} "
                f"trades={row['n_trades']}",
                flush=True,
            )
            results.append(row)
            (out / f"metrics_{name}.json").write_text(json.dumps(row, indent=2), encoding="utf-8")

        results.sort(key=lambda r: -r["ret"])
        path = out / "alpha_calendar_summary.json"
        path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print("\n=== TOP ===", flush=True)
        for r in results:
            print(f"  {r['variant']}: ret={r['ret']:.2f}% sharpe={r['sharpe']:.3f}", flush=True)
        print("DONE", path, flush=True)


if __name__ == "__main__":
    main()
