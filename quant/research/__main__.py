"""研究平台 CLI。"""

from __future__ import annotations

import argparse
import json

from quant.backtest.broker import BrokerConfig
from quant.backtest.engine import run_backtest
from quant.config import load_quant_config
from quant.ml.dataset import load_score_samples
from quant.monitoring.drift import detect_and_act, detect_ic_drift
from quant.monitoring.parity import run_parity_check, scan_parity
from quant.factors.report import run_factor_research
from quant.research.execution_sensitivity import execution_sensitivity_grid
from quant.research.factor.ablation import dimension_ablation_ic
from quant.research.factor.ic import factor_report
from quant.research.promote import run_promote_cli
from quant.research.registry import create_experiment, save_experiment
from quant.research.report import render_report_html, write_report
from quant.research.significance import bootstrap_sharpe, permutation_test_sharpe


def _daily_returns_from_backtest(metrics: dict) -> list[float]:
    return metrics.get("_daily_returns") or []


def _observed_sharpe(daily: list[float]) -> float:
    import numpy as np

    arr = np.array(daily, dtype=float)
    if len(arr) < 2:
        return 0.0
    std = arr.std(ddof=1)
    if std < 1e-9:
        return 0.0
    return float(arr.mean() / std * np.sqrt(252))


def _print(obj: object, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))
    else:
        print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def cmd_backtest(args: argparse.Namespace) -> None:
    exp = create_experiment(
        hypothesis=args.hypothesis or "backtest",
        data_from=args.from_date or "",
        data_to=args.to_date or "",
        mode=args.mode,
    )
    cfg = BrokerConfig(initial_cash=args.cash)
    metrics = run_backtest(
        from_date=args.from_date,
        to_date=args.to_date,
        broker_cfg=cfg,
        mode=args.mode,
    )
    daily = _daily_returns_from_backtest(metrics)
    if daily:
        n_boot = int((load_quant_config().get("research") or {}).get("bootstrap_samples", 1000))
        obs = _observed_sharpe(daily)
        metrics["observed_sharpe"] = round(obs, 4)
        metrics["bootstrap_sharpe"] = bootstrap_sharpe(daily, n_samples=n_boot, seed=42)
        metrics["permutation_sharpe"] = permutation_test_sharpe(
            daily,
            obs,
            n_perm=min(500, n_boot),
            seed=42,
        )
    if getattr(args, "sensitivity", False):
        metrics["execution_sensitivity"] = execution_sensitivity_grid(
            from_date=args.from_date,
            to_date=args.to_date,
            mode=args.mode,
        )
    extra = {}
    if args.report:
        html = render_report_html(experiment=exp.to_dict(), metrics=metrics)
        write_report(exp.artifacts_dir / "report.html", html)
        extra["report"] = "report.html"
    path = save_experiment(exp, metrics=metrics, extra_files=extra)
    if args.json:
        print(json.dumps(metrics, ensure_ascii=False, indent=2))
    else:
        print(f"实验 {exp.id} 已保存: {path}")
        for k, v in metrics.items():
            if not k.startswith("_"):
                print(f"{k}: {v}")


def cmd_factor(_args: argparse.Namespace) -> None:
    samples = load_score_samples()
    report = factor_report(samples)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def cmd_factors(args: argparse.Namespace) -> None:
    """行业/市值中性化因子：原始 vs 中性化 RankIC。"""
    cfg = (load_quant_config().get("research") or {}).get("factors") or {}
    horizon = int(args.horizon or cfg.get("holding_horizon_days", 5))
    min_names = int(args.min_names or cfg.get("min_cross_section", 5))
    out = run_factor_research(
        from_date=args.from_date,
        to_date=args.to_date,
        horizon=horizon,
        min_names=min_names,
    )
    _print(out, as_json=True)


def cmd_ablation(args: argparse.Namespace) -> None:
    dims = [d.strip() for d in (args.dims or "").split(",") if d.strip()] or None
    out = dimension_ablation_ic(dims=dims)
    _print(out, as_json=True)


def cmd_promote(args: argparse.Namespace) -> None:
    out = run_promote_cli(apply=bool(args.apply))
    _print(out, as_json=True)


def cmd_drift(args: argparse.Namespace) -> None:
    if args.apply:
        out = detect_and_act(
            apply=True,
            dry_run=bool(args.dry_run),
            recent_days=args.recent_days,
            baseline_days=args.baseline_days,
        )
    else:
        out = detect_ic_drift(
            recent_days=args.recent_days,
            baseline_days=args.baseline_days,
        )
    _print(out, as_json=True)


def cmd_parity(args: argparse.Namespace) -> None:
    if args.date:
        out = run_parity_check(args.date, min_parity_ratio=args.min_ratio)
    else:
        out = scan_parity(recent_days=args.recent_days, min_parity_ratio=args.min_ratio)
    _print(out, as_json=True)


def cmd_sensitivity(args: argparse.Namespace) -> None:
    grid = execution_sensitivity_grid(
        from_date=args.from_date,
        to_date=args.to_date,
        mode=args.mode,
    )
    print(json.dumps(grid, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="GoldQuant 量化研究平台")
    sub = parser.add_subparsers(dest="cmd")

    p_bt = sub.add_parser("backtest", help="注册实验并运行回测")
    p_bt.add_argument("--from", dest="from_date", default=None)
    p_bt.add_argument("--to", dest="to_date", default=None)
    p_bt.add_argument("--cash", type=float, default=100_000.0)
    p_bt.add_argument(
        "--mode",
        default="full_system",
        choices=["full_system", "intraday_replay"],
    )
    p_bt.add_argument("--hypothesis", default="")
    p_bt.add_argument("--report", action="store_true")
    p_bt.add_argument("--sensitivity", action="store_true", help="附带滑点敏感性网格")
    p_bt.add_argument("--json", action="store_true")
    p_bt.set_defaults(func=cmd_backtest)

    p_f = sub.add_parser("factor", help="维度日度 RankIC 报告")
    p_f.set_defaults(func=cmd_factor)

    p_fs = sub.add_parser("factors", help="中性化因子层：原始 vs 中性 RankIC")
    p_fs.add_argument("--from", dest="from_date", default=None)
    p_fs.add_argument("--to", dest="to_date", default=None)
    p_fs.add_argument("--horizon", type=int, default=0, help="持有期交易日；0=读配置")
    p_fs.add_argument("--min-names", type=int, default=0, help="截面最少股票数；0=读配置")
    p_fs.set_defaults(func=cmd_factors)

    p_ab = sub.add_parser("ablation", help="维度 ablation（去掉后 IC 变化）")
    p_ab.add_argument("--dims", default="", help="逗号分隔维度；空=全部")
    p_ab.set_defaults(func=cmd_ablation)

    p_pr = sub.add_parser("promote", help="晋升门禁评估（可选写校准草案）")
    p_pr.add_argument("--apply", action="store_true", help="门禁通过时写 ml_calibration_draft.yml")
    p_pr.set_defaults(func=cmd_promote)

    p_d = sub.add_parser("drift", help="IC 漂移监控")
    p_d.add_argument("--recent-days", type=int, default=30)
    p_d.add_argument("--baseline-days", type=int, default=90)
    p_d.add_argument("--apply", action="store_true", help="对翻转维度写降权覆盖")
    p_d.add_argument("--dry-run", action="store_true", help="只打印动作不写文件")
    p_d.set_defaults(func=cmd_drift)

    p_p = sub.add_parser("parity", help="纸交易/信号 parity")
    p_p.add_argument("--date", default=None, help="单日 YYYY-MM-DD")
    p_p.add_argument("--recent-days", type=int, default=5)
    p_p.add_argument("--min-ratio", type=float, default=0.8)
    p_p.set_defaults(func=cmd_parity)

    p_s = sub.add_parser("sensitivity", help="执行敏感性网格")
    p_s.add_argument("--from", dest="from_date", default=None)
    p_s.add_argument("--to", dest="to_date", default=None)
    p_s.add_argument("--mode", default="intraday_replay")
    p_s.set_defaults(func=cmd_sensitivity)

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return
    args.func(args)


if __name__ == "__main__":
    main()
