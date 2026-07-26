"""回测脚本：离线库 + 因子面板 + TargetPortfolio + 出场规则。

用法：
    python -m scripts.backtest.run --start 2022-01-01 --end 2024-12-31 --out reports/bt
"""

from __future__ import annotations

import argparse
from datetime import date

from quant.backtest2.engine import ExitConfig, run_backtest
from quant.backtest2.report import export_report
from quant.data.calendar import to_iso, trading_day_list
from quant.data.industry import read_industry_snapshot
from quant.data.store import read_daily_raw
from quant.factors.compose import compose_alpha
from quant.factors.panel_builder import build_panel
from quant.portfolio2.target import TargetPortfolio


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--out", default="reports/bt")
    ap.add_argument("--max-positions", type=int, default=10)
    ap.add_argument("--n-enter", type=int, default=8)
    ap.add_argument("--n-exit", type=int, default=15)
    ap.add_argument("--target-vol", type=float, default=0.15)
    ap.add_argument("--no-exit", action="store_true", help="关闭时序出场")
    ap.add_argument(
        "--loose",
        action="store_true",
        help="T 日收盘信号/收盘成交（乐观上界）；默认 strict=T-1 信号/T 开盘",
    )
    args = ap.parse_args()

    dates = [
        to_iso(d)
        for d in trading_day_list(date.fromisoformat(args.start), date.fromisoformat(args.end))
    ]
    if not dates:
        print("无交易日")
        return

    daily = read_daily_raw()
    panel = build_panel(dates, daily=daily)
    print(f"面板 {len(panel)} 行")

    alpha_by_date: dict[str, dict[str, float]] = {}
    rows_by_date: dict[str, list] = {}
    for r in panel:
        rows_by_date.setdefault(r.date, []).append(r)
    for d, rows in rows_by_date.items():
        alpha_by_date[d] = compose_alpha(rows)

    def alpha_fn(d: str, _rows: dict) -> dict[str, float]:
        return alpha_by_date.get(d, {})

    # 行业映射：取区间末日快照作组合约束（更严 PIT 可按日注入，此处简化）
    sectors = read_industry_snapshot(dates[-1]) if dates else {}

    policy = TargetPortfolio(
        n_enter=args.n_enter,
        n_exit=args.n_exit,
        max_stocks=args.max_positions,
        target_vol=args.target_vol,
        daily=daily,
        sectors=sectors,
    )
    exit_cfg = None if args.no_exit else ExitConfig()
    broker = run_backtest(
        daily=daily,
        dates=dates,
        alpha_fn=alpha_fn,
        policy=policy,
        max_positions=args.max_positions,
        exit_config=exit_cfg,
        strict_signals=not args.loose,
    )
    rep = export_report(broker, args.out)
    print(rep["metrics_data"])


if __name__ == "__main__":
    main()
