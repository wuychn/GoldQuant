"""导出 inv_t10_e20 的示例买卖回合。"""

from __future__ import annotations

import pickle
from collections import defaultdict
from datetime import date
from pathlib import Path

from quant.backtest.engine import run_backtest
from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import to_iso, trading_day_list
from scripts.cli_home import home_context
from scripts.research.swing_alpha_calendar import AlphaCalendarPolicy


def main() -> None:
    with home_context(r"D:\ProgramData\.quant"):
        out = Path(r"D:\ProgramData\.quant\reports\bt_swing_band")
        with open(out / "alpha_2023-08-01_2025-12-31.pkl", "rb") as f:
            abd = pickle.load(f)
        daily = load_adjusted_daily()
        dates = [
            to_iso(d) for d in trading_day_list(date(2023, 8, 1), date(2025, 12, 31))
        ]
        pol = AlphaCalendarPolicy(
            abd, dates, topn=10, every=20, invert=True, max_weight=0.095
        )

        def af(d, _r, _a=abd):
            return _a.get(d, {"__pad__": 1.0})

        print("run_backtest …", flush=True)
        broker = run_backtest(
            daily=daily,
            dates=dates,
            alpha_fn=af,
            policy=pol,
            max_positions=10,
            exit_config=None,
            strict_signals=True,
            drawdown_halt=None,
        )
        if not broker.trades:
            print("no trades", flush=True)
            return
        t0 = broker.trades[0]
        print("trade_attrs", sorted(a for a in dir(t0) if not a.startswith("_")), flush=True)
        print("sample", t0, flush=True)

        by: dict[str, list] = defaultdict(list)
        for t in broker.trades:
            by[str(t.code)].append(t)

        # name lookup from daily last known
        names = {}
        if "name" in daily.columns:
            for code, g in daily.groupby(daily["code"].astype(str)):
                n = g["name"].dropna()
                if len(n):
                    names[str(code)] = str(n.iloc[-1])

        samples = []
        for code, ts in sorted(by.items(), key=lambda kv: -len(kv[1])):
            ts = sorted(ts, key=lambda x: (str(x.date), 0 if str(x.side).lower() == "buy" else 1))
            rounds = []
            open_buys = []
            for t in ts:
                side = str(t.side).lower()
                if side == "buy":
                    open_buys.append(t)
                elif side == "sell" and open_buys:
                    b = open_buys.pop(0)
                    rounds.append((b, t))
            if rounds:
                samples.append((code, rounds, len(ts)))
            if len(samples) >= 3:
                break

        print("\n=== 3 stocks ===", flush=True)
        for code, rounds, n in samples:
            name = names.get(code, "")
            print(f"\n{code} {name}  (trades={n}, rounds={len(rounds)})", flush=True)
            for i, (b, s) in enumerate(rounds[:3], 1):
                bp = float(b.price)
                sp = float(s.price)
                ret = sp / bp - 1.0 if bp else 0.0
                print(
                    f"  回合{i}: 买 {b.date} @ {bp:.4f} → 卖 {s.date} @ {sp:.4f}  "
                    f"({ret*100:+.2f}%) shares={getattr(b,'shares',None)}",
                    flush=True,
                )


if __name__ == "__main__":
    main()
