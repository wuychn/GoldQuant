"""动量策略官方回测（与纸面同一套规则）。

用法::

    poetry run python -m scripts.backtest.run_momentum --home D:/ProgramData/.quant
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import to_iso, trading_day_list
from quant.swing.momentum import MomentumConfig
from quant.swing.momentum_bt import build_momentum_bt_data, curve_metrics, run_dual_slot_cn
from scripts.cli_home import add_home_argument, home_context


def _write_monthly(curve: list[tuple[str, float]], path: Path, by_year: dict) -> None:
    df = pd.DataFrame(curve, columns=["date", "eq"])
    df["date"] = pd.to_datetime(df["date"])
    lines: list[str] = []
    for (y, mo), g in df.groupby([df["date"].dt.year, df["date"].dt.month]):
        r = float(g["eq"].iloc[-1] / g["eq"].iloc[0] - 1.0) * 100
        lines.append(f"{int(y)}-{int(mo):02d} {r:.2f}")
    for y, r in by_year.items():
        lines.append(f"YEAR {y} {r}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="动量双槽官方回测（T+1 开盘买 / hold 日后收盘卖）")
    add_home_argument(ap)
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2026-08-14")
    ap.add_argument("--ma", type=int, default=None)
    ap.add_argument("--topn", type=int, default=None)
    ap.add_argument("--min-adv", type=float, default=None)
    ap.add_argument("--hold", type=int, default=None)
    ap.add_argument("--cash", type=float, default=1_000_000.0)
    ap.add_argument("--max-idle", type=int, default=None)
    ap.add_argument("--out", default=None, help="默认 $QUANT_HOME/reports/bt_momentum")
    args = ap.parse_args()

    with home_context(args.home):
        from quant.store.paths import quant_home

        cfg0 = MomentumConfig.from_config()
        ma = args.ma if args.ma is not None else cfg0.ma
        topn = args.topn if args.topn is not None else cfg0.topn
        min_adv = args.min_adv if args.min_adv is not None else cfg0.min_adv
        hold = args.hold if args.hold is not None else cfg0.hold_days
        max_idle_n = args.max_idle if args.max_idle is not None else cfg0.max_idle
        out = Path(args.out) if args.out else Path(quant_home()) / "reports/bt_momentum"
        out.mkdir(parents=True, exist_ok=True)
        daily = load_adjusted_daily()
        daily = pd.DataFrame({c: np.asarray(daily[c]) for c in daily.columns})
        end = min(args.end, pd.to_datetime(daily["date"]).max().strftime("%Y-%m-%d"))
        dates = [
            to_iso(d)
            for d in trading_day_list(date.fromisoformat(args.start), date.fromisoformat(end))
        ]
        frames = build_momentum_bt_data(
            daily,
            dates,
            min_adv=min_adv,
            listed_days=cfg0.listed_days,
            ma=ma,
            index_code=cfg0.index_code,
        )
        gate = frames["gate"]
        max_idle = max_idle_n if max_idle_n > 0 else None
        report = {
            "name": "momentum_swing",
            "window": f"{dates[0]} ~ {dates[-1]}",
            "n_days": len(dates),
            "cost_model": "commission 1bp/side min 5yuan, stamp 5bp sell, SH transfer 0.1bp",
            "rules": {
                "universe": f"non-ST, exclude BJ, ADV20>={min_adv:.0f}, listed>={cfg0.listed_days}, no close limit-up",
                "alpha": f"T close ret1 Top{topn}",
                "gate": (
                    f"HS300 close[T] > MA{ma} lagged; after {max_idle_n} cash days force half-slot"
                    if max_idle_n > 0
                    else f"HS300 close[T] > MA{ma} lagged"
                ),
                "exec": f"buy T+1 open, sell after {hold} closes, {cfg0.n_slots}-slot, skip open limit-up",
            },
            "variants": {},
        }
        for cash, hold_days in ((args.cash, hold),):
            curve, trades, cst = run_dual_slot_cn(
                dates=dates,
                close=frames["close"],
                open_=frames["open_"],
                ranks=frames["ranks"],
                lu_open=frames["lu_open"],
                bull_lag=gate,
                topn=topn,
                max_gap=None,
                hold_days=hold_days,
                n_slots=cfg0.n_slots,
                initial_cash=cash,
                max_idle=max_idle,
            )
            m = curve_metrics(curve)
            trets = [t["ret"] for t in trades if t.get("ret") is not None]
            streaks = cst.get("cash_streaks") or []
            cst_pub = {k: v for k, v in cst.items() if k != "cash_streaks"}
            pack = {
                **m,
                "n_trades": len(trades),
                "win_rate": round(float(np.mean(np.array(trets) > 0) * 100), 1) if trets else 0,
                "avg_trade_pct": round(float(np.mean(trets)) * 100, 3) if trets else 0,
                "gate_on_days": int(gate.sum()),
                "cash": cash,
                "hold_days": hold_days,
                "max_idle": max_idle,
                **cst_pub,
                "long_streaks_ge15": [s for s in streaks if s["n"] >= 15],
            }
            report["primary"] = pack
            report["variants"]["primary"] = pack
            print(
                f"ann={m.get('ann')} ret={m.get('ret')} mdd={m.get('mdd')} "
                f"inv={cst.get('invested_pct')}% idleMax={cst.get('max_cash_streak_post60')} "
                f"{m.get('by_year')}",
                flush=True,
            )
            pd.DataFrame(curve, columns=["date", "eq"]).to_csv(out / "curve.csv", index=False)
            _write_monthly(curve, out / "monthly.txt", m.get("by_year") or {})

        path = out / "report.json"
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print("WROTE", path, flush=True)


if __name__ == "__main__":
    main()
