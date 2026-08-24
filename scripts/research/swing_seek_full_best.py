"""完整 broker：WF + ATR trail + 月熔断（当前快筛最优参数）。"""

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
from quant.data.calendar import to_iso, trading_day_list, trading_days_between
from quant.exit.atr import atr
from scripts.cli_home import add_home_argument, home_context
from scripts.research.swing_monthly10_explore import monthly_stats
from scripts.research.swing_seek_best import score_row
from scripts.research.swing_seek_fast import precompute_ranks
from scripts.research.swing_seek_wf_refine import sim_fixed


class WfTrailHaltPolicy:
    def __init__(
        self,
        alpha_by_date,
        dates,
        ranks,
        daily,
        *,
        topn=1,
        every=20,
        lb_months=4,
        min_edge=0.05,
        trail_atr=3.05,
        month_halt=-0.17,
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
        self.trail_atr = trail_atr
        self.month_halt = month_halt
        self.full_invest = full_invest
        self.max_weight = full_invest
        self._frozen = {}
        self._offset = 0
        self._risk_on = True
        self._month_halted = False
        self._last_month = None
        self._month_start_eq = None
        self._peak = {}
        self.holding_snapshots = {}
        self._by_code = {}
        d = daily.copy()
        if not pd.api.types.is_string_dtype(d["date"]):
            d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
        d["code"] = d["code"].astype(str)
        self._by_code = {str(c): g.sort_values("date").reset_index(drop=True) for c, g in d.groupby("code")}
        self._eq_mat = None
        self._eq_path = []  # (date, eq_proxy) for month halt — use broker equity via snapshots hack
        self._broker_eq_ref = None

    def bind_ret(self, ret: pd.DataFrame) -> None:
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

    def _update_regime(self, as_of: str) -> None:
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
        self._month_halted = False
        self._last_month = mon
        self._month_start_eq = None  # reset; filled on first equity observe

    def _atr_last(self, code: str, as_of: str) -> float | None:
        g = self._by_code.get(code)
        if g is None or g.empty:
            return None
        hist = g[g["date"] <= as_of]
        if len(hist) < 20:
            return None
        a = float(atr(hist, 14).iloc[-1])
        return a if np.isfinite(a) and a > 0 else None

    def target_weights(self, alpha, prices, current, date):
        as_of = str(date)[:10]
        i = self.date_i.get(as_of)
        if i is None:
            return {}
        self._update_regime(as_of)

        # 用当前持仓市值近似权益变化做月熔断（无 broker 句柄时退化为只 trail）
        # 简化：若持仓相对成本回撤过大，由 trail 处理；月熔断用持仓未实现亏损总和近似
        if current and self.holding_snapshots:
            # 估计组合浮盈：sum(w * (px/cost-1))
            pnl = 0.0
            for c, w in current.items():
                if not w or w <= 0:
                    continue
                snap = self.holding_snapshots.get(c) or {}
                cost = float(snap.get("cost") or 0)
                px = float(prices.get(c) or 0)
                if cost > 0 and px > 0:
                    pnl += float(w) * (px / cost - 1.0)
            # 若本月尚未记起点，用 0
            if self._month_start_eq is None:
                self._month_start_eq = 0.0
            # 用累计近似：若当日组合相对成本亏超过 halt，熔断
            # 更稳：跟踪本月权益 — 此处用 running min 近似
            if pnl <= self.month_halt:
                self._month_halted = True

        if not self._risk_on or self._month_halted:
            self._frozen = {}
            return {}

        # trail：持仓跌破 peak - trail*ATR → 卖
        keep = {}
        for c, w in (current or {}).items():
            if not w or w <= 1e-9:
                continue
            px = float(prices.get(c) or 0)
            if px <= 0:
                continue
            self._peak[c] = max(self._peak.get(c, px), px)
            a = self._atr_last(c, as_of)
            if a is not None and px <= self._peak[c] - self.trail_atr * a:
                continue
            keep[c] = float(w)

        do_reb = ((i - self._offset) % max(1, self.every) == 0) or (not keep and not self._frozen)
        if do_reb and len(keep) < self.topn:
            picked = [c for c in self.ranks.get(as_of, []) if c in (prices or {}) and c not in keep][
                : self.topn - len(keep)
            ]
            slots = picked
            budget = max(0.0, self.full_invest - sum(keep.values()))
            if slots and budget > 0:
                w_each = min(budget / len(slots), self.max_weight)
                for c in slots:
                    keep[c] = w_each
                    self._peak[c] = float(prices.get(c) or 0)
            self._frozen = dict(keep)
        elif keep:
            self._frozen = dict(keep)
        else:
            # 无持仓且非换仓日：沿用 frozen 若仍有效
            if not self._frozen:
                return {}
            # 检查 frozen trail
            out = {}
            for c, w in self._frozen.items():
                if c not in (prices or {}):
                    continue
                px = float(prices[c])
                self._peak[c] = max(self._peak.get(c, px), px)
                a = self._atr_last(c, as_of)
                if a is not None and px <= self._peak[c] - self.trail_atr * a:
                    continue
                out[c] = w
            self._frozen = out
            return out

        return {c: w for c, w in self._frozen.items() if w > 1e-6}


def main() -> None:
    ap = argparse.ArgumentParser()
    add_home_argument(ap)
    ap.add_argument("--start", default="2023-08-01")
    ap.add_argument("--end", default="2025-12-31")
    args = ap.parse_args()
    with home_context(args.home):
        from quant.store.paths import quant_home

        out = Path(quant_home()) / "reports/bt_swing_seek"
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

        pol = WfTrailHaltPolicy(alpha, dates, ranks, daily, trail_atr=3.05, month_halt=-0.17)
        pol.bind_ret(ret)

        def alpha_fn(dd, _r):
            return alpha.get(dd, {"__pad__": 1.0})

        print("full BT…", flush=True)
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
        ms = monthly_stats(broker.equity_curve)
        row = {
            "variant": "full_wf_trail305_halt17",
            "ret": float(m["total_return_pct"]),
            "ann": float(m["ann_return_pct"]),
            "sharpe": float(m["sharpe"]),
            "mdd": float(m["max_drawdown_pct"]),
            "n_trades": int(m["n_trades"]),
            "turn_ann": float(m["turnover_annual"]),
            "avg_hold": float(m["avg_hold_days"]),
            **{k: v for k, v in ms.items() if k not in ("monthly",)},
            "monthly": ms.get("monthly"),
        }
        row["score"] = round(score_row(row), 3)
        path = out / "full_best_trail_halt.json"
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
