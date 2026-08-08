"""盘中择时日频代理回测脚本（P1）。

验证 ``compose_intraday_alpha`` 的 θ 择时在历史上是否有增益（触发组 vs 池内全部）。

用法::

    python -m scripts.backtest.run_intraday_timing --start 2023-01-01 --end 2024-12-31
    python -m scripts.backtest.run_intraday_timing --start 2023-01-01 --end 2024-12-31 --theta 0.8 --pool 30
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from common.progress_log import log_progress_done, log_progress_error, log_progress_start
from quant.backtest.intraday_timing import run_intraday_timing_backtest
from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import to_iso, trading_day_list
from quant.factors.compose import compose_alpha
from quant.factors.panel_builder import build_panel
from quant.store.paths import reports_dir
from scripts.cli_home import add_home_argument, home_context

_SCOPE = "backtest.intraday_timing"


def main() -> None:
    ap = argparse.ArgumentParser(description="盘中择时日频代理回测：验证 θ 择时历史增益")
    add_home_argument(ap)
    ap.add_argument("--start", required=True, help="回测起始日 YYYY-MM-DD（含）")
    ap.add_argument("--end", required=True, help="回测结束日 YYYY-MM-DD（含）")
    ap.add_argument("--theta", type=float, default=1.0, help="盘中择时触发阈值 θ（默认 1.0，越低触发越多）")
    ap.add_argument("--pool", type=int, default=30, help="每日 alpha 作战池规模（默认 30）")
    ap.add_argument("--horizon", type=int, default=5, help="持有期评估窗口（交易日，默认 5）")
    ap.add_argument("--out", default=None, help="JSON 报告路径；默认 $QUANT_HOME/reports/intraday_timing/theta_{θ}.json")
    args = ap.parse_args()

    log_progress_start(
        _SCOPE,
        "开始",
        detail=f"{args.start} ~ {args.end} theta={args.theta} pool={args.pool}",
    )
    try:
        with home_context(args.home):
            dates = [
                to_iso(d)
                for d in trading_day_list(date.fromisoformat(args.start), date.fromisoformat(args.end))
            ]
            if not dates:
                log_progress_error(_SCOPE, "失败", detail="无交易日")
                sys.exit(1)

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
            log_progress_done(_SCOPE, "成功", detail=out)
    except SystemExit:
        raise
    except Exception as e:
        log_progress_error(_SCOPE, "失败", detail=f"{type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    main()
