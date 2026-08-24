"""反转 alpha + 灵活调仓（非日历整仓置换）：趋势坏强制卖；机会成本门槛换仓；排除 ST。

回应：
- 不要「每月整体换仓」的死板
- 趋势走坏 / 有明显更好标的时才动
- 尽量降低「接飞刀赌博感」：过滤 ST、要求最低流动性（在选股侧用 alpha 宇宙+名称）
"""

from __future__ import annotations

import argparse
import json
import pickle
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from quant.backtest.engine import run_backtest
from quant.backtest.metrics import compute_metrics
from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import to_iso, trading_day_list
from quant.data.universe import _is_st_name
from quant.portfolio.trend_state import trend_fail_streak
from scripts.cli_home import add_home_argument, home_context
from scripts.research.swing_monthly10_explore import monthly_stats


@dataclass
class _Pos:
    buy_date: str
    highest_close: float


@dataclass
class InvertFlexPolicy:
    alpha_by_date: dict[str, dict[str, float]]
    daily: pd.DataFrame
    invert: bool = True
    max_stocks: int = 3
    n_enter: int = 5  # 新开仓须进入分数前 n_enter
    n_keep: int = 15  # 排名仍 ≤ n_keep 且趋势 OK → 不因外部更强被换
    delta_sigma: float = 0.4
    trend_fail_days: int = 2
    trail_atr_mult: float = 3.0
    full_invest: float = 0.95
    max_weight: float = 0.40
    exclude_st: bool = True
    min_hold_days: int = 5  # 未满持有天数不换仓（可趋势强平）
    _pos: dict[str, _Pos] = field(default_factory=dict)
    _by_code: dict[str, pd.DataFrame] = field(default_factory=dict, repr=False)
    _names: dict[str, str] = field(default_factory=dict)
    holding_snapshots: dict = field(default_factory=dict)
    last_actions: dict[str, str] = field(default_factory=dict)

    @property
    def n(self) -> int:
        return self.max_stocks

    def bind(self) -> None:
        d = self.daily
        if not pd.api.types.is_string_dtype(d["date"]):
            d = d.copy()
            d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
        d = d.copy()
        d["code"] = d["code"].astype(str)
        self.daily = d
        self._by_code = {
            str(c): g.sort_values("date").reset_index(drop=True) for c, g in d.groupby("code")
        }
        if "name" in d.columns:
            for code, g in d.groupby("code"):
                n = g["name"].dropna()
                if len(n):
                    self._names[str(code)] = str(n.iloc[-1])

    def _scores(self, as_of: str, prices: dict) -> list[tuple[str, float]]:
        raw = dict(self.alpha_by_date.get(as_of) or {})
        if self.invert:
            raw = {c: -float(v) for c, v in raw.items()}
        out = []
        for c, s in raw.items():
            if c not in prices:
                continue
            if self.exclude_st and _is_st_name(self._names.get(c, "")):
                continue
            out.append((c, float(s)))
        out.sort(key=lambda x: -x[1])
        return out

    def _hist(self, code: str, as_of: str) -> pd.DataFrame:
        g = self._by_code.get(str(code))
        if g is None or g.empty:
            return pd.DataFrame()
        return g[g["date"] <= as_of]

    def _sync(self, current, prices, as_of: str) -> None:
        held = {c for c, w in (current or {}).items() if w and w > 1e-9}
        for c in list(self._pos.keys()):
            if c not in held:
                self._pos.pop(c, None)
        for c in held:
            px = float(prices.get(c) or 0.0)
            snap = (self.holding_snapshots or {}).get(c) or {}
            if c not in self._pos:
                buy = str(snap.get("buy_date") or as_of)[:10]
                self._pos[c] = _Pos(buy_date=buy, highest_close=max(px, 0.0))
            else:
                if px > self._pos[c].highest_close:
                    self._pos[c].highest_close = px
                if snap.get("buy_date"):
                    self._pos[c].buy_date = str(snap["buy_date"])[:10]

    def _hold_days(self, buy: str, as_of: str) -> int:
        try:
            from quant.data.calendar import trading_days_between
            from datetime import date as date_cls

            return int(
                trading_days_between(
                    date_cls.fromisoformat(buy[:10]),
                    date_cls.fromisoformat(as_of[:10]),
                )
            )
        except Exception:
            return 0

    def target_weights(self, alpha, prices, current, date):
        as_of = str(date)[:10]
        self.last_actions = {}
        self._sync(current or {}, prices or {}, as_of)
        ranked = self._scores(as_of, prices or {})
        score_of = {c: s for c, s in ranked}
        rank_of = {c: i + 1 for i, (c, _) in enumerate(ranked)}

        # 1) 趋势坏 → 强制卖
        held: list[str] = []
        for code, w in (current or {}).items():
            if not w or w <= 1e-9:
                continue
            st = self._pos.get(code)
            if st is None:
                held.append(code)
                continue
            hist = self._hist(code, as_of)
            streak = trend_fail_streak(
                hist,
                buy_date=st.buy_date,
                atr_mult=self.trail_atr_mult,
            )
            if streak >= self.trend_fail_days:
                self.last_actions[code] = "trend_force"
                continue
            held.append(code)

        # 2) 机会成本换仓（需过门槛，且旧票不在 keep 区或已超 min_hold）
        vals = np.array([s for _, s in ranked], dtype=float) if ranked else np.array([1.0])
        sigma = float(np.nanstd(vals, ddof=1)) if len(vals) > 1 else 1.0
        if sigma < 1e-9:
            sigma = 1.0
        delta = self.delta_sigma * sigma

        def can_replace(code: str) -> bool:
            r = rank_of.get(code)
            if r is not None and r <= self.n_keep:
                return False  # 仍在可持有区，不因外部更强踢掉
            st = self._pos.get(code)
            if st and self._hold_days(st.buy_date, as_of) < self.min_hold_days:
                return False
            return True

        for new_c, new_s in ranked:
            if len(held) < self.max_stocks:
                break
            if new_c in held:
                continue
            # 新票须够强
            if rank_of.get(new_c, 999) > self.n_enter:
                continue
            # 找最弱可换
            replaceable = [
                (score_of.get(c, -1e18), c) for c in held if can_replace(c)
            ]
            if not replaceable:
                break
            replaceable.sort()
            old_s, old_c = replaceable[0]
            if new_s - old_s < delta:
                continue
            held.remove(old_c)
            held.append(new_c)
            self.last_actions[old_c] = "swap_out"
            self.last_actions[new_c] = "swap_in"

        # 3) 空位：仅纳入前 n_enter
        for c, _s in ranked:
            if len(held) >= self.max_stocks:
                break
            if c in held:
                continue
            if rank_of.get(c, 999) > self.n_enter:
                continue
            held.append(c)
            self.last_actions[c] = self.last_actions.get(c) or "enter"

        held = held[: self.max_stocks]
        if not held:
            return {}
        w = min(self.full_invest / len(held), self.max_weight)
        return {c: w for c in held}


def main() -> None:
    ap = argparse.ArgumentParser()
    add_home_argument(ap)
    ap.add_argument("--start", default="2023-08-01")
    ap.add_argument("--end", default="2025-12-31")
    args = ap.parse_args()

    with home_context(args.home):
        from quant.store.paths import quant_home

        out = Path(quant_home()) / "reports/bt_invert_flex"
        out.mkdir(parents=True, exist_ok=True)
        cache = (
            Path(quant_home())
            / "reports/bt_swing_band"
            / f"alpha_{args.start}_{args.end}.pkl"
        )
        with open(cache, "rb") as f:
            abd = pickle.load(f)
        daily = load_adjusted_daily()
        dates = [
            to_iso(d)
            for d in trading_day_list(
                date.fromisoformat(args.start), date.fromisoformat(args.end)
            )
        ]

        jobs = [
            ("invflex_t3_k15", dict(max_stocks=3, n_enter=5, n_keep=15, delta_sigma=0.4, min_hold_days=5)),
            ("invflex_t3_k20", dict(max_stocks=3, n_enter=8, n_keep=20, delta_sigma=0.5, min_hold_days=8)),
            ("invflex_t3_strict", dict(max_stocks=3, n_enter=3, n_keep=12, delta_sigma=0.6, min_hold_days=10)),
            ("invflex_t5_k20", dict(max_stocks=5, n_enter=8, n_keep=20, delta_sigma=0.4, min_hold_days=5)),
        ]
        results = []
        for name, kw in jobs:
            print(f"\n=== {name} ===", flush=True)
            pol = InvertFlexPolicy(alpha_by_date=abd, daily=daily, invert=True, **kw)
            pol.bind()
            pol.max_weight = min(0.45, 0.95 / kw["max_stocks"])

            def af(d, _r, _a=abd):
                return _a.get(d, {"__pad__": 1.0})

            t0 = time.perf_counter()
            broker = run_backtest(
                daily=daily,
                dates=dates,
                alpha_fn=af,
                policy=pol,
                max_positions=kw["max_stocks"],
                exit_config=None,
                strict_signals=True,
                drawdown_halt=None,
            )
            m = compute_metrics(broker, daily=None)
            ms = monthly_stats(broker.equity_curve)
            row = {
                "variant": name,
                "ret": float(m["total_return_pct"]),
                "sharpe": float(m["sharpe"]),
                "mdd": float(m["max_drawdown_pct"]),
                "n_trades": int(m["n_trades"]),
                "turn_ann": float(m["turnover_annual"]),
                "avg_hold": float(m["avg_hold_days"]),
                "win_rate": float(m["win_rate"]),
                "sec": round(time.perf_counter() - t0, 1),
                **{k: v for k, v in ms.items() if k not in ("monthly",)},
                **kw,
            }
            print(
                f"{name}: ret={row['ret']:.1f}% sharpe={row['sharpe']:.2f} mdd={row['mdd']} "
                f"hold={row['avg_hold']} turn={row['turn_ann']:.0f} ge10={row.get('pct_months_ge_10')}%",
                flush=True,
            )
            results.append(row)
            (out / f"metrics_{name}.json").write_text(
                json.dumps(row, indent=2, default=str), encoding="utf-8"
            )

        results.sort(key=lambda r: -r["ret"])
        path = out / "summary.json"
        path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        print("\n=== TOP ===", flush=True)
        for r in results:
            print(f"  {r['variant']}: {r['ret']:.1f}%", flush=True)
        print("DONE", path, flush=True)


if __name__ == "__main__":
    main()
