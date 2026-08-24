"""导出 inv_t3_e20 若干完整买卖回合（大白话核对用）。"""

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
            abd, dates, topn=3, every=20, invert=True, max_weight=0.95 / 3
        )

        def af(d, _r, _a=abd):
            return _a.get(d, {"__pad__": 1.0})

        print("running inv_t3_e20 …", flush=True)
        broker = run_backtest(
            daily=daily,
            dates=dates,
            alpha_fn=af,
            policy=pol,
            max_positions=3,
            exit_config=None,
            strict_signals=True,
            drawdown_halt=None,
        )

        names: dict[str, str] = {}
        if "name" in daily.columns:
            for code, g in daily.groupby(daily["code"].astype(str)):
                n = g["name"].dropna()
                if len(n):
                    names[str(code)] = str(n.iloc[-1])

        by_code: dict[str, list] = defaultdict(list)
        for t in broker.trades:
            by_code[str(t.code)].append(t)

        spells_all = []
        for code, ts in by_code.items():
            ts = sorted(
                ts,
                key=lambda x: (str(x.date), 0 if str(x.side).lower() == "buy" else 1),
            )
            pos = 0
            entry_date = None
            entry_cost = 0.0
            entry_shares = 0
            for t in ts:
                side = str(t.side).lower()
                sh = int(t.shares)
                px = float(t.price)
                if side == "buy":
                    if pos == 0:
                        entry_date = str(t.date)[:10]
                        entry_cost = 0.0
                        entry_shares = 0
                    pos += sh
                    entry_cost += px * sh
                    entry_shares += sh
                elif side == "sell":
                    pos -= sh
                    if pos <= 0 and entry_date is not None:
                        avg = entry_cost / entry_shares if entry_shares else 0.0
                        hold_days = (
                            date.fromisoformat(str(t.date)[:10])
                            - date.fromisoformat(entry_date)
                        ).days
                        spells_all.append(
                            {
                                "code": code,
                                "name": names.get(code, ""),
                                "buy": entry_date,
                                "sell": str(t.date)[:10],
                                "avg_buy": avg,
                                "sell_px": px,
                                "shares": entry_shares,
                                "ret": (px / avg - 1.0) if avg else 0.0,
                                "hold_cal_days": hold_days,
                            }
                        )
                        pos = 0
                        entry_date = None

        # 选：持股约 15~45 自然日、股数较大的几段（更像整仓进出）
        mid = [
            s
            for s in spells_all
            if 15 <= s["hold_cal_days"] <= 50 and s["shares"] >= 1000
        ]
        mid.sort(key=lambda s: -abs(s["ret"]))
        # 再补几段赚钱的、亏钱的各一些，方便对照
        wins = [s for s in mid if s["ret"] > 0.05][:4]
        losses = [s for s in mid if s["ret"] < -0.05][:2]
        picked = []
        seen = set()
        for s in wins + losses + mid:
            key = (s["code"], s["buy"])
            if key in seen:
                continue
            seen.add(key)
            picked.append(s)
            if len(picked) >= 6:
                break

        print("\nSTRATEGY=inv_t3_e20 invert IC top3 every20d strict open", flush=True)
        print(f"spells_total={len(spells_all)} shown={len(picked)}\n", flush=True)
        for s in picked:
            print(
                f"{s['code']} {s['name']}\n"
                f"  BUY  {s['buy']}  ~{s['avg_buy']:.3f}\n"
                f"  SELL {s['sell']}  ~{s['sell_px']:.3f}\n"
                f"  ret={s['ret']*100:+.1f}%  hold≈{s['hold_cal_days']}d  shares={s['shares']}\n",
                flush=True,
            )


if __name__ == "__main__":
    main()
