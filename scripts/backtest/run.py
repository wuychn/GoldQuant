"""回测脚本：用离线库 + 因子面板跑端到端回测。

用法：
    python -m scripts.backtest.run --start 2022-01-01 --end 2024-12-31 --out reports/bt
"""

from __future__ import annotations

import argparse

import pandas as pd

from quant.backtest2.engine import run_backtest
from quant.backtest2.policy import EqualWeightTopN
from quant.backtest2.report import export_report
from quant.data.calendar import to_iso, trading_day_list
from quant.data.store import read_daily_raw
from quant.factors.compose import compose_alpha
from quant.factors.panel_builder import build_panel


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--out", default="reports/bt")
    ap.add_argument("--max-positions", type=int, default=10)
    ap.add_argument("--strict", action="store_true", help="T-1 信号 / T 开盘成交")
    args = ap.parse_args()

    from datetime import date

    dates = [to_iso(d) for d in trading_day_list(date.fromisoformat(args.start), date.fromisoformat(args.end))]
    if not dates:
        print("无交易日")
        return

    daily = read_daily_raw()
    panel = build_panel(dates, daily=daily)
    print(f"面板 {len(panel)} 行")

    # 预算每个日期的 alpha
    alpha_by_date: dict[str, dict[str, float]] = {}
    rows_by_date: dict[str, list] = {}
    for r in panel:
        rows_by_date.setdefault(r.date, []).append(r)
    for d, rows in rows_by_date.items():
        alpha_by_date[d] = compose_alpha(rows)

    def alpha_fn(d: str, _rows: dict) -> dict[str, float]:
        return alpha_by_date.get(d, {})

    policy = EqualWeightTopN(n=args.max_positions)
    broker = run_backtest(
        daily=daily, dates=dates, alpha_fn=alpha_fn, policy=policy,
        max_positions=args.max_positions, strict_signals=args.strict,
    )
    rep = export_report(broker, args.out)
    print(rep["metrics_data"])


if __name__ == "__main__":
    main()
