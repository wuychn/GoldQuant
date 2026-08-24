"""固化当前最优：WF invert Top1 e20 lb4 + 轻微空仓门控；并做完整 broker 回测。"""

from __future__ import annotations

import argparse
import json
import pickle
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
from scripts.research.swing_seek_best import score_row
from scripts.research.swing_seek_fast import precompute_ranks
from scripts.research.swing_seek_wf_refine import walk_forward


class WalkForwardInvertPolicy:
    """每月用近 lb 个月最优相位；若最优区间收益≤min_edge 则空仓。"""

    def __init__(
        self,
        alpha_by_date,
        dates,
        ranks,
        *,
        topn=1,
        every=20,
        lb_months=4,
        min_edge=0.05,
        full_invest=0.95,
    ):
        self.alpha_by_date = alpha_by_date
        self.dates = list(dates)
        self.date_i = {d: i for i, d in enumerate(self.dates)}
        self.ranks = ranks
        self.topn = topn
        self.every = every
        self.lb_months = lb_months
        self.min_edge = min_edge
        self.full_invest = full_invest
        self.max_weight = full_invest
        self._frozen = {}
        self._offset = 0
        self._risk_on = True
        self._last_month = None
        self.holding_snapshots = {}
        # 预计算各相位权益供滚动评分
        from scripts.research.swing_seek_wf_refine import sim_fixed

        # 需要 ret panel — 延迟在 bind 时注入
        self._eq_mat = None

    def bind_ret(self, ret: pd.DataFrame) -> None:
        from scripts.research.swing_seek_wf_refine import sim_fixed

        curves = {
            o: sim_fixed(ret, self.ranks, self.dates, self.topn, self.every, o)
            for o in range(self.every)
        }
        self._eq_mat = {
            o: pd.Series({pd.Timestamp(d): e for d, e in c}) for o, c in curves.items()
        }

    @property
    def n(self) -> int:
        return self.topn

    def _update_month(self, as_of: str) -> None:
        if self._eq_mat is None:
            return
        mon = pd.Timestamp(as_of).to_period("M")
        if self._last_month is None:
            self._last_month = mon
            return
        if mon == self._last_month:
            return
        start_m = mon - self.lb_months
        scores = {}
        for o, s in self._eq_mat.items():
            mask = (s.index.to_period("M") >= start_m) & (s.index.to_period("M") < mon)
            sub = s.loc[mask]
            scores[o] = float(sub.iloc[-1] / sub.iloc[0] - 1.0) if len(sub) >= 5 else -1e9
        self._offset, best = max(scores.items(), key=lambda kv: kv[1])
        self._risk_on = best > self.min_edge
        self._last_month = mon

    def target_weights(self, alpha, prices, current, date):
        as_of = str(date)[:10]
        i = self.date_i.get(as_of)
        if i is None:
            return {}
        self._update_month(as_of)
        if not self._risk_on:
            self._frozen = {}
            return {}
        do_reb = ((i - self._offset) % max(1, self.every) == 0) or (not self._frozen)
        if do_reb:
            picked = [c for c in self.ranks.get(as_of, []) if c in (prices or {})][: self.topn]
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
            alpha = pickle.load(f)
        d = daily.copy()
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
        d["code"] = d["code"].astype(str)
        d = d[d["date"].isin(dates)]
        close = d.pivot_table(index="date", columns="code", values="close", aggfunc="last").reindex(dates)
        ret = close.pct_change()
        ranks = precompute_ranks(alpha, dates, set(close.columns.astype(str)))

        # 快筛确认
        curve = walk_forward(
            ret, ranks, dates, 1, 20, lb_months=4, cash_if_best_neg=True, min_edge=0.05
        )
        ms = monthly_stats(curve)
        print("fast WF confirm", ms.get("mean_month_pct"), ms.get("pct_months_ge_10"), flush=True)

        pol = WalkForwardInvertPolicy(alpha, dates, ranks, topn=1, every=20, lb_months=4, min_edge=0.05)
        pol.bind_ret(ret)

        def alpha_fn(dd, _r):
            return alpha.get(dd, {"__pad__": 1.0})

        print("full BT WF…", flush=True)
        broker = run_backtest(
            daily=daily,
            dates=dates,
            alpha_fn=alpha_fn,
            policy=pol,
            max_positions=1,
            exit_config=None,
            strict_signals=True,
            drawdown_halt=None,
        )
        m = compute_metrics(broker, daily=None)
        ms2 = monthly_stats(broker.equity_curve)
        row = {
            "variant": "wf_inv_t1_e20_lb4_cash5",
            "ret": float(m["total_return_pct"]),
            "ann": float(m["ann_return_pct"]),
            "sharpe": float(m["sharpe"]),
            "mdd": float(m["max_drawdown_pct"]),
            "n_trades": int(m["n_trades"]),
            "turn_ann": float(m["turnover_annual"]),
            "avg_hold": float(m["avg_hold_days"]),
            **{k: v for k, v in ms2.items() if k not in ("monthly",)},
            "fast_mean_month": ms.get("mean_month_pct"),
            "monthly": ms2.get("monthly"),
        }
        row["score"] = round(score_row(row), 3)
        path = out / "best_strategy.json"
        path.write_text(json.dumps(row, indent=2, default=str), encoding="utf-8")
        print(
            f"FULL ret={row['ret']:.1f}% meanM={row.get('mean_month_pct')} "
            f"ge10={row.get('pct_months_ge_10')}% med={row.get('median_month_pct')} "
            f"mdd={row['mdd']} sharpe={row['sharpe']}",
            flush=True,
        )
        print("DONE", path, flush=True)


if __name__ == "__main__":
    main()
