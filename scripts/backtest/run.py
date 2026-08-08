"""回测脚本（更新：IC 权重 + MVO + 敏感性 + strict 标注）。

用法::

    poetry run python -m scripts.backtest.run --home D:/ProgramData/.quant \\
        --start 2024-01-01 --end 2026-08-07 --workers 4
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date

from common.progress_log import log_progress, log_progress_done, log_progress_error, log_progress_start
from scripts.cli_home import add_home_argument, home_context
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
    ap = argparse.ArgumentParser(description="IC 权重 + MVO 回测并导出报告")
    add_home_argument(ap)
    ap.add_argument("--start", required=True, help="回测起始日 YYYY-MM-DD（含）")
    ap.add_argument("--end", required=True, help="回测结束日 YYYY-MM-DD（含）")
    ap.add_argument("--out", default=None, help="报告输出目录；默认 $QUANT_HOME/reports/bt")
    ap.add_argument("--max-positions", type=int, default=10, help="最大持仓只数（默认 10）")
    ap.add_argument("--n-enter", type=int, default=8, help="目标组合纳入阈值排名（默认 8）")
    ap.add_argument("--n-exit", type=int, default=15, help="目标组合剔除阈值排名（默认 15）")
    ap.add_argument("--target-vol", type=float, default=0.15, help="目标组合年化波动率（默认 0.15）")
    ap.add_argument("--no-exit", action="store_true", help="关闭出场规则（仅测 alpha 选股）")
    ap.add_argument("--loose", action="store_true", help="关闭 strict 开盘成交（非官方口径）")
    ap.add_argument("--registry-weights", action="store_true", help="忽略 IC 权重，用 registry 默认")
    ap.add_argument("--sensitivity", action="store_true", help="额外输出参数敏感性扫描")
    ap.add_argument(
        "--workers",
        type=int,
        default=1,
        help="因子面板按股票并行进程数（默认 1；建议 2–4，内存紧张保持 1）",
    )
    ap.add_argument(
        "--sens-workers",
        type=int,
        default=1,
        help="敏感性扫描并行进程数（默认 1；仅 --sensitivity 时生效）",
    )
    args = ap.parse_args()
    if args.workers < 1:
        ap.error("--workers 须 >= 1")
    if args.sens_workers < 1:
        ap.error("--sens-workers 须 >= 1")

    log_progress_start(
        _SCOPE,
        "开始",
        detail=(
            f"home={args.home or '当前'} {args.start} ~ {args.end} "
            f"workers={args.workers} sens_workers={args.sens_workers}"
        ),
    )
    try:
        with home_context(args.home):
            strict = not args.loose
            out = args.out or str(reports_dir("bt"))
            dates = [
                to_iso(d)
                for d in trading_day_list(date.fromisoformat(args.start), date.fromisoformat(args.end))
            ]
            if not dates:
                log_progress_error(_SCOPE, "失败", detail="无交易日")
                sys.exit(1)

            t0 = time.perf_counter()
            daily = load_adjusted_daily()
            log_progress(
                _SCOPE,
                "构建 alpha …",
                detail=f"workers={args.workers}",
            )
            t_alpha0 = time.perf_counter()
            alpha_by_date = build_alpha_by_date(
                dates,
                daily,
                use_ic_weights=not args.registry_weights,
                workers=args.workers,
            )
            t_alpha = time.perf_counter() - t_alpha0
            print(f"alpha 覆盖 {len(alpha_by_date)} 日 · 耗时 {t_alpha:.1f}s", flush=True)

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
            log_progress(_SCOPE, "跑回测 …", detail=f"{len(dates)} 日（串行）")
            t_bt0 = time.perf_counter()
            broker = run_backtest(
                daily=daily,
                dates=dates,
                alpha_fn=alpha_fn,
                policy=policy,
                max_positions=args.max_positions,
                exit_config=exit_cfg,
                strict_signals=strict,
            )
            t_bt = time.perf_counter() - t_bt0
            print(f"回测耗时 {t_bt:.1f}s", flush=True)

            sens = None
            if args.sensitivity:
                log_progress(
                    _SCOPE,
                    "敏感性扫描 …",
                    detail=f"sens_workers={args.sens_workers}",
                )
                t_s0 = time.perf_counter()
                defaults = {
                    "n_enter": float(args.n_enter),
                    "n_exit": float(args.n_exit),
                    "target_vol": float(args.target_vol),
                    "buffer_abs": 0.01,
                    "sector_cap": 0.40,
                }
                if args.sens_workers <= 1:

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

                    sens = run_backtest_sensitivity(
                        base_run_fn=_sens_run, workers=1
                    )
                else:
                    sens = run_backtest_sensitivity(
                        workers=args.sens_workers,
                        parallel_ctx={
                            "daily": daily,
                            "dates": dates,
                            "alpha_by_date": alpha_by_date,
                            "max_positions": args.max_positions,
                            "strict_signals": strict,
                            "use_exit": not args.no_exit,
                            "defaults": defaults,
                        },
                    )
                print(
                    f"敏感性耗时 {time.perf_counter() - t_s0:.1f}s",
                    flush=True,
                )

            rep = export_report(broker, out, daily=daily, strict_signals=strict, sensitivity=sens)
            print(rep["metrics_data"])
            log_progress_done(
                _SCOPE,
                "成功",
                detail=f"{out} · 总耗时 {time.perf_counter() - t0:.1f}s",
            )
    except SystemExit:
        raise
    except Exception as e:
        log_progress_error(_SCOPE, "失败", detail=f"{type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    main()
