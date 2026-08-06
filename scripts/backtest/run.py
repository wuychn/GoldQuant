"""回测脚本（更新：IC 权重 + MVO + 敏感性 + strict 标注）。"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from common.progress_log import log_progress, log_progress_done, log_progress_error, log_progress_start
from quant.backtest.engine import ExitConfig, run_backtest
from quant.backtest.metrics import compute_metrics
from quant.backtest.report import export_report
from quant.backtest.sensitivity_report import run_backtest_sensitivity
from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import to_iso, trading_day_list
from quant.data.industry import read_industry_snapshot
from quant.factors.alpha_builder import build_alpha_by_date
from quant.portfolio.target import TargetPortfolio
from quant.store.paths import reports_dir

_SCOPE = "backtest.run"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--out", default=None, help="默认 $QUANT_HOME/reports/bt")
    ap.add_argument("--max-positions", type=int, default=10)
    ap.add_argument("--n-enter", type=int, default=8)
    ap.add_argument("--n-exit", type=int, default=15)
    ap.add_argument("--target-vol", type=float, default=0.15)
    ap.add_argument("--no-exit", action="store_true")
    ap.add_argument("--loose", action="store_true", help="关闭 strict 开盘成交（非官方口径）")
    ap.add_argument("--registry-weights", action="store_true", help="忽略 IC 权重")
    ap.add_argument("--sensitivity", action="store_true", help="输出参数敏感性")
    args = ap.parse_args()

    log_progress_start(_SCOPE, "开始", detail=f"{args.start} ~ {args.end}")
    try:
        strict = not args.loose
        out = args.out or str(reports_dir("bt"))
        dates = [
            to_iso(d)
            for d in trading_day_list(date.fromisoformat(args.start), date.fromisoformat(args.end))
        ]
        if not dates:
            log_progress_error(_SCOPE, "失败", detail="无交易日")
            sys.exit(1)

        daily = load_adjusted_daily()
        log_progress(_SCOPE, "构建 alpha …")
        alpha_by_date = build_alpha_by_date(dates, daily, use_ic_weights=not args.registry_weights)
        print(f"alpha 覆盖 {len(alpha_by_date)} 日")

        def alpha_fn(d: str, _rows: dict) -> dict[str, float]:
            return alpha_by_date.get(d, {})

        policy = TargetPortfolio.from_config(
            n_enter=args.n_enter,
            n_exit=args.n_exit,
            max_stocks=args.max_positions,
            target_vol=args.target_vol,
            daily=daily,
            sectors=read_industry_snapshot(dates[-1]) if dates else {},
        )
        exit_cfg = None if args.no_exit else ExitConfig()
        log_progress(_SCOPE, "跑回测 …", detail=f"{len(dates)} 日")
        broker = run_backtest(
            daily=daily,
            dates=dates,
            alpha_fn=alpha_fn,
            policy=policy,
            max_positions=args.max_positions,
            exit_config=exit_cfg,
            strict_signals=strict,
        )

        sens = None
        if args.sensitivity:

            def _sens_run(params: dict[str, float]) -> float:
                ne = int(params.get("n_enter", args.n_enter))
                tv = float(params.get("target_vol", args.target_vol))
                ba = float(params.get("buffer_abs", 0.01))
                sc = float(params.get("sector_cap", 0.40))
                pol = TargetPortfolio.from_config(
                    n_enter=ne,
                    n_exit=max(ne + 5, args.n_exit),
                    max_stocks=args.max_positions,
                    target_vol=tv,
                    buffer_abs=ba,
                    sector_cap=sc,
                    daily=daily,
                )
                b = run_backtest(
                    daily=daily,
                    dates=dates,
                    alpha_fn=alpha_fn,
                    policy=pol,
                    max_positions=args.max_positions,
                    exit_config=exit_cfg,
                    strict_signals=strict,
                )
                return float(compute_metrics(b).get("sharpe") or 0.0)

            sens = run_backtest_sensitivity(base_run_fn=_sens_run)

        rep = export_report(broker, out, daily=daily, strict_signals=strict, sensitivity=sens)
        print(rep["metrics_data"])
        log_progress_done(_SCOPE, "成功", detail=out)
    except SystemExit:
        raise
    except Exception as e:
        log_progress_error(_SCOPE, "失败", detail=f"{type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    main()
