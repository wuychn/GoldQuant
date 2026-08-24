"""从 inv_t10_e20 成交重建完整持仓回合（净仓 0→>0→0）。"""

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

        names: dict[str, str] = {}
        if "name" in daily.columns:
            for code, g in daily.groupby(daily["code"].astype(str)):
                n = g["name"].dropna()
                if len(n):
                    names[str(code)] = str(n.iloc[-1])

        # 按日累计净仓，切出完整回合
        by_code: dict[str, list] = defaultdict(list)
        for t in broker.trades:
            by_code[str(t.code)].append(t)

        results = []
        for code, ts in by_code.items():
            ts = sorted(ts, key=lambda x: (str(x.date), 0 if str(x.side).lower() == "buy" else 1))
            pos = 0
            entry_date = None
            entry_cost = 0.0  # 累计买入金额
            entry_shares = 0
            spells = []
            for t in ts:
                side = str(t.side).lower()
                sh = int(t.shares)
                px = float(t.price)
                if side == "buy":
                    if pos == 0:
                        entry_date = str(t.date)
                        entry_cost = 0.0
                        entry_shares = 0
                    pos += sh
                    entry_cost += px * sh
                    entry_shares += sh
                elif side == "sell":
                    pos -= sh
                    if pos <= 0 and entry_date is not None:
                        # 用本回合加权均价估收益（简化：卖出价相对买入均价）
                        avg_buy = entry_cost / entry_shares if entry_shares else 0.0
                        spells.append(
                            {
                                "buy_date": entry_date,
                                "sell_date": str(t.date),
                                "avg_buy": avg_buy,
                                "sell_px": px,
                                "shares": entry_shares,
                                "ret": (px / avg_buy - 1.0) if avg_buy else 0.0,
                            }
                        )
                        pos = 0
                        entry_date = None
            if spells:
                # 选持股天数适中的一回合（接近 20 日换仓）
                best = max(spells, key=lambda s: s["shares"])
                results.append((code, names.get(code, ""), best, len(spells), len(ts)))

        # 取完整持股量最大的 3 只（更像整仓进出）
        results.sort(key=lambda x: -x[2]["shares"])
        print("\n策略: 反转IC alpha, Top10, 每20交易日换仓, strict T-1信号/T开盘成交", flush=True)
        print("区间总收益对应窗口: 2023-08-01 ~ 2025-12-31 (inv_t10_e20 ≈ +68.6%)\n", flush=True)
        for code, name, sp, n_spells, n_tr in results[:3]:
            print(f"{code} {name}", flush=True)
            print(
                f"  买入 {sp['buy_date']}  均价≈{sp['avg_buy']:.4f}  股数={sp['shares']}",
                flush=True,
            )
            print(
                f"  卖出 {sp['sell_date']}  价≈{sp['sell_px']:.4f}  "
                f"本回合约 {sp['ret']*100:+.2f}%  (该票共{n_spells}个完整回合)",
                flush=True,
            )
            print(flush=True)


if __name__ == "__main__":
    main()
