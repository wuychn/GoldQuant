"""自主寻优：逼近「月收益≥10%」目标。

不交互；按证据迭代多族策略，按 mean_month / pct_months_ge_10 / total_ret 打分，
写出 leaderboard，直到跑完本轮网格。

策略族：
  1) invert 日历冻结（已验证有边）
  2) 宽度差门控：仅宽度差时满仓 invert，否则降仓/空仓
  3) 宽度差时 TopK 更集中
  4) invert flex 激进参数
"""

from __future__ import annotations

import argparse
import json
import pickle
import time
from collections import defaultdict
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
from quant.factors.alpha_builder import build_alpha_by_date
from scripts.cli_home import add_home_argument, home_context
from scripts.research.swing_alpha_calendar import AlphaCalendarPolicy
from scripts.research.swing_monthly10_explore import monthly_stats


def score_row(r: dict) -> float:
    """综合分：优先月均与≥10%命中，兼顾总收益与回撤。"""
    mean_m = float(r.get("mean_month_pct") or 0)
    ge10 = float(r.get("pct_months_ge_10") or 0)
    ret = float(r.get("ret") or 0)
    mdd = abs(float(r.get("mdd") or 0))
    # 月均逼近/超过 10 给高权重；命中率；总收益；惩罚深回撤
    return mean_m * 3.0 + ge10 * 0.8 + ret * 0.02 - mdd * 0.15


def build_breadth(daily: pd.DataFrame, start: str, end: str, min_adv: float = 1.5e8) -> dict[str, float]:
    d = daily.copy()
    d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
    d["code"] = d["code"].astype(str)
    if "name" not in d.columns:
        d["name"] = ""
    if "amount" not in d.columns:
        d["amount"] = 0.0
    day_above: dict[str, list[int]] = defaultdict(list)
    for code, g in d.groupby("code", sort=False):
        g = g.sort_values("date").reset_index(drop=True)
        if len(g) < 80:
            continue
        if _is_st_name(str(g["name"].iloc[-1])):
            continue
        close = pd.to_numeric(g["close"], errors="coerce").to_numpy(dtype=float)
        amount = pd.to_numeric(g["amount"], errors="coerce").to_numpy(dtype=float)
        adv = pd.Series(amount).rolling(20, min_periods=10).mean().to_numpy()
        ma50 = pd.Series(close).rolling(50).mean().to_numpy()
        dates = g["date"].astype(str).to_numpy()
        for i in range(50, len(g)):
            dt = dates[i]
            if dt < start or dt > end:
                continue
            if not np.isfinite(adv[i]) or float(adv[i]) < min_adv:
                continue
            if not np.isfinite(ma50[i]) or close[i] <= 0:
                continue
            day_above[dt].append(1 if close[i] > ma50[i] else 0)
    return {dt: float(np.mean(vs)) for dt, vs in day_above.items() if len(vs) >= 80}


@dataclass
class BreadthGateCalendarPolicy:
    """宽度门控日历：宽度差时用 invert TopN；宽度好时降仓或空仓。"""

    alpha_by_date: dict
    dates: list[str]
    breadth: dict[str, float]
    topn: int = 3
    every: int = 10
    invert: bool = True
    breadth_max: float = 0.42  # ≤ 此宽度才满仓
    off_scale: float = 0.0  # 宽度好时仓位比例（0=空仓）
    full_invest: float = 0.95
    max_weight: float = 0.45
    _frozen: dict[str, float] = field(default_factory=dict)
    _last_reb_i: int = -999
    holding_snapshots: dict = field(default_factory=dict)
    date_i: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.date_i = {d: i for i, d in enumerate(self.dates)}

    @property
    def n(self) -> int:
        return self.topn

    def target_weights(self, alpha, prices, current, date):
        as_of = str(date)[:10]
        i = self.date_i.get(as_of)
        if i is None:
            return {}
        br = self.breadth.get(as_of)
        risk_on = br is not None and br <= self.breadth_max
        scale = self.full_invest if risk_on else self.off_scale

        do_reb = (i % max(1, self.every) == 0) or (not self._frozen)
        if do_reb:
            raw = dict(self.alpha_by_date.get(as_of) or alpha or {})
            if self.invert:
                raw = {c: -float(v) for c, v in raw.items()}
            ranked = sorted(raw.items(), key=lambda kv: -kv[1])
            picked = [c for c, _ in ranked if c in (prices or {})][: self.topn]
            if not picked or scale <= 1e-9:
                self._frozen = {}
                return {}
            w = min(scale / len(picked), self.max_weight)
            self._frozen = {c: w for c in picked}
            self._last_reb_i = i
        elif not risk_on and self.off_scale <= 1e-9:
            # 非换仓日若宽度转好且要求空仓 → 清仓
            self._frozen = {}
            return {}
        elif not risk_on and self.off_scale > 0 and self._frozen:
            # 降仓：等比缩放
            s = sum(self._frozen.values()) or 1.0
            factor = self.off_scale / s
            self._frozen = {c: min(v * factor, self.max_weight) for c, v in self._frozen.items()}
        return {c: w for c, w in self._frozen.items() if c in (prices or {}) and w > 1e-6}


def run_one(daily, dates, alpha_by_date, policy, max_pos: int) -> dict:
    def alpha_fn(d, _rows, _abd=alpha_by_date):
        return _abd.get(d, {"__pad__": 1.0})

    t0 = time.perf_counter()
    broker = run_backtest(
        daily=daily,
        dates=dates,
        alpha_fn=alpha_fn,
        policy=policy,
        max_positions=max_pos,
        exit_config=None,
        strict_signals=True,
        drawdown_halt=None,
    )
    m = compute_metrics(broker, daily=None)
    ms = monthly_stats(broker.equity_curve)
    row = {
        "ret": float(m["total_return_pct"]),
        "ann": float(m["ann_return_pct"]),
        "sharpe": float(m["sharpe"]),
        "mdd": float(m["max_drawdown_pct"]),
        "n_trades": int(m["n_trades"]),
        "turn_ann": float(m["turnover_annual"]),
        "avg_hold": float(m["avg_hold_days"]),
        "win_rate": float(m["win_rate"]),
        "sec": round(time.perf_counter() - t0, 1),
        **{k: v for k, v in ms.items() if k not in ("monthly", "months_ge_10")},
        "months_ge_10": ms.get("months_ge_10"),
    }
    row["score"] = round(score_row(row), 3)
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    add_home_argument(ap)
    ap.add_argument("--start", default="2023-08-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    with home_context(args.home):
        from quant.store.paths import quant_home

        out = Path(quant_home()) / "reports/bt_swing_seek"
        out.mkdir(parents=True, exist_ok=True)
        print("load daily…", flush=True)
        daily = load_adjusted_daily()
        dates = [
            to_iso(d)
            for d in trading_day_list(date.fromisoformat(args.start), date.fromisoformat(args.end))
        ]
        print(f"dates={len(dates)} {dates[0]}~{dates[-1]}", flush=True)

        cache = Path(quant_home()) / "reports/bt_swing_band" / f"alpha_{args.start}_{args.end}.pkl"
        cache.parent.mkdir(parents=True, exist_ok=True)
        if cache.exists():
            print("load alpha", cache, flush=True)
            with open(cache, "rb") as f:
                alpha_by_date = pickle.load(f)
        else:
            print("build alpha…", flush=True)
            t0 = time.perf_counter()
            alpha_by_date = build_alpha_by_date(dates, daily, workers=args.workers)
            with open(cache, "wb") as f:
                pickle.dump(alpha_by_date, f)
            print(f"alpha {time.perf_counter()-t0:.1f}s", flush=True)

        br_path = out / f"breadth_{args.start}_{args.end}.pkl"
        if br_path.exists():
            with open(br_path, "rb") as f:
                breadth = pickle.load(f)
            print("load breadth", len(breadth), flush=True)
        else:
            print("build breadth…", flush=True)
            breadth = build_breadth(daily, args.start, args.end)
            with open(br_path, "wb") as f:
                pickle.dump(breadth, f)
            print("breadth days", len(breadth), flush=True)

        results: list[dict] = []

        # —— 族1：invert 日历密集扫 ——
        print("\n=== FAMILY 1: invert calendar ===", flush=True)
        for topn in (1, 2, 3, 5, 8):
            for every in (5, 8, 10, 15, 20):
                name = f"inv_t{topn}_e{every}"
                pol = AlphaCalendarPolicy(
                    alpha_by_date,
                    dates,
                    topn=topn,
                    every=every,
                    invert=True,
                    max_weight=min(0.95, max(0.12, 0.95 / topn)),
                )
                row = run_one(daily, dates, alpha_by_date, pol, topn)
                row["variant"] = name
                row["family"] = "inv_cal"
                print(
                    f"{name}: ret={row['ret']:.1f}% meanM={row.get('mean_month_pct')} "
                    f"ge10={row.get('pct_months_ge_10')}% score={row['score']}",
                    flush=True,
                )
                results.append(row)
                (out / f"metrics_{name}.json").write_text(
                    json.dumps(row, indent=2, default=str), encoding="utf-8"
                )

        # —— 族2：宽度门控 ——
        print("\n=== FAMILY 2: breadth gate ===", flush=True)
        for topn in (2, 3, 5):
            for every in (5, 10, 15):
                for bmax in (0.35, 0.42, 0.50):
                    for off in (0.0, 0.30):
                        name = f"bg_t{topn}_e{every}_b{int(bmax*100)}_off{int(off*100)}"
                        pol = BreadthGateCalendarPolicy(
                            alpha_by_date=alpha_by_date,
                            dates=dates,
                            breadth=breadth,
                            topn=topn,
                            every=every,
                            invert=True,
                            breadth_max=bmax,
                            off_scale=off,
                            max_weight=min(0.50, 0.95 / topn),
                        )
                        row = run_one(daily, dates, alpha_by_date, pol, topn)
                        row["variant"] = name
                        row["family"] = "breadth_gate"
                        print(
                            f"{name}: ret={row['ret']:.1f}% meanM={row.get('mean_month_pct')} "
                            f"ge10={row.get('pct_months_ge_10')}% score={row['score']}",
                            flush=True,
                        )
                        results.append(row)
                        (out / f"metrics_{name}.json").write_text(
                            json.dumps(row, indent=2, default=str), encoding="utf-8"
                        )

        results.sort(key=lambda r: -r["score"])
        best = results[0]
        summary = {
            "start": args.start,
            "end": args.end,
            "n_variants": len(results),
            "best": best,
            "top10": results[:10],
            "target": {
                "mean_month_ge_10": float(best.get("mean_month_pct") or 0) >= 10.0,
                "pct_ge10": best.get("pct_months_ge_10"),
                "mean_month_pct": best.get("mean_month_pct"),
            },
            "all": results,
        }
        path = out / "seek_summary.json"
        path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        print("\n=== LEADERBOARD ===", flush=True)
        for r in results[:15]:
            print(
                f"  {r['variant']}: score={r['score']} ret={r['ret']:.1f}% "
                f"meanM={r.get('mean_month_pct')} ge10={r.get('pct_months_ge_10')}% "
                f"mdd={r.get('mdd')}",
                flush=True,
            )
        hit = float(best.get("mean_month_pct") or 0) >= 10.0
        print(
            f"\nBEST={best['variant']} mean_month={best.get('mean_month_pct')} "
            f"target_mean>=10: {hit}",
            flush=True,
        )
        print("DONE", path, flush=True)


if __name__ == "__main__":
    main()
