"""盘中择时日频代理回测脚本（P1）。

验证 ``compose_intraday_alpha`` 的 θ 择时在历史上是否有增益（触发组 vs 池内全部）。

用法::

    python -m scripts.backtest.run_intraday_timing --start 2023-01-01 --end 2024-12-31
    python -m scripts.backtest.run_intraday_timing --start 2023-01-01 --end 2024-12-31 --theta 0.8 --pool 30
"""

from __future__ import annotations

import argparse
import json
from datetime import date

from quant.backtest.intraday_timing import run_intraday_timing_backtest
from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import to_iso, trading_day_list
from quant.factors.compose import compose_alpha
from quant.factors.panel_builder import build_panel
from quant.store.paths import reports_dir


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--theta", type=float, default=1.0)
    ap.add_argument("--pool", type=int, default=30)
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    dates = [
        to_iso(d)
        for d in trading_day_list(date.fromisoformat(args.start), date.fromisoformat(args.end))
    ]
    if not dates:
        print("无交易日")
        return

    daily = load_adjusted_daily()
    panel = build_panel(dates, daily=daily)
    rows_by_date: dict[str, list] = {}
    for r in panel:
        rows_by_date.setdefault(r.date, []).append(r)
    alpha_by_date = {d: compose_alpha(rows) for d, rows in rows_by_date.items()}

    res = run_intraday_timing_backtest(
        daily=daily, dates=dates, alpha_by_date=alpha_by_date,
        theta=args.theta, pool_size=args.pool, horizon=args.horizon,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))
    print("\n=== 判定 ===")
    if res["triggered"]["n"] == 0:
        print(f"θ={args.theta} 过高/池太小 → 无触发；建议下调 θ 或扩大池")
    elif res["edge"] > 0:
        print(f"OK: 触发组 {res['triggered']['mean_ret']*100:.2f}% > 池内 {res['pool_all']['mean_ret']*100:.2f}%（edge {res['edge']*100:+.2f}%）→ θ 择时有增益")
    else:
        print(f"WARN: edge {res['edge']*100:+.2f}% ≤ 0 → θ 择时无增益，建议重标定 θ 或检查盘中因子")

    out = args.out or str(reports_dir("intraday_timing") / f"theta_{args.theta}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(f"\n已写入 {out}")


if __name__ == "__main__":
    main()
