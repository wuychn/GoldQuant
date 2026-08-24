"""换仓相位敏感性：every=20 时 offset=0..19。"""

from __future__ import annotations

import argparse
import json
import pickle
from datetime import date
from pathlib import Path

from quant.backtest.engine import run_backtest
from quant.backtest.metrics import compute_metrics
from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import to_iso, trading_day_list
from scripts.cli_home import add_home_argument, home_context
from scripts.research.swing_alpha_calendar import AlphaCalendarPolicy


class PhasePol(AlphaCalendarPolicy):
    def __init__(self, *a, phase_offset: int = 0, **k):
        super().__init__(*a, **k)
        self.phase_offset = int(phase_offset)

    def target_weights(self, alpha, prices, current, date):
        as_of = str(date)[:10]
        i = self.date_i.get(as_of)
        if i is None:
            return {c: w for c, w in self._frozen.items() if c in (prices or {})}
        do_reb = ((i - self.phase_offset) % max(1, self.every) == 0) or (not self._frozen)
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
    args = ap.parse_args()
    with home_context(args.home):
        from quant.store.paths import quant_home

        out = Path(quant_home()) / "reports/bt_swing_band"
        with open(out / "alpha_2023-08-01_2025-12-31.pkl", "rb") as f:
            abd = pickle.load(f)
        daily = load_adjusted_daily()
        dates = [
            to_iso(d)
            for d in trading_day_list(date(2023, 8, 1), date(2025, 12, 31))
        ]
        rows = []
        for offset in range(20):
            pol = PhasePol(
                abd,
                dates,
                topn=10,
                every=20,
                invert=True,
                max_weight=0.095,
                phase_offset=offset,
            )

            def af(d, _r, _a=abd):
                return _a.get(d, {"__pad__": 1.0})

            b = run_backtest(
                daily=daily,
                dates=dates,
                alpha_fn=af,
                policy=pol,
                max_positions=10,
                exit_config=None,
                strict_signals=True,
                drawdown_halt=None,
            )
            m = compute_metrics(b, daily=None)
            row = {
                "offset": offset,
                "ret": float(m["total_return_pct"]),
                "sharpe": float(m["sharpe"]),
                "mdd": float(m["max_drawdown_pct"]),
            }
            print(
                f"offset={offset}: ret={row['ret']:.2f}% sharpe={row['sharpe']:.3f} mdd={row['mdd']:.1f}",
                flush=True,
            )
            rows.append(row)
        rets = sorted(r["ret"] for r in rows)
        summary = {
            "best": max(rows, key=lambda r: r["ret"]),
            "worst": min(rows, key=lambda r: r["ret"]),
            "median_ret": rets[len(rets) // 2],
            "mean_ret": sum(rets) / len(rets),
            "rows": rows,
        }
        (out / "phase_sensitivity.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )
        print(
            f"mean={summary['mean_ret']:.2f}% median={summary['median_ret']:.2f}% "
            f"best={summary['best']['ret']:.2f}% worst={summary['worst']['ret']:.2f}%",
            flush=True,
        )
        print("DONE", out / "phase_sensitivity.json", flush=True)


if __name__ == "__main__":
    main()
