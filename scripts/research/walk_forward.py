"""Walk-forward + DSR + 参数敏感性入口。

用法：
    python -m scripts.research.walk_forward --start 2020-01-01 --end 2024-12-31 --out reports/wf
"""

from __future__ import annotations

import argparse
import itertools
import json
from datetime import date
from pathlib import Path

from quant.backtest.engine import ExitConfig, run_backtest
from quant.backtest.metrics import compute_metrics
from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import to_iso, trading_day_list
from quant.factors.compose import compose_alpha
from quant.factors.panel_builder import build_panel
from quant.portfolio.target import TargetPortfolio
from quant.research.sensitivity import parameter_budget, scan_param
from quant.research.significance import deflated_sharpe
from quant.research.walk_forward import walk_forward


def _build_alpha(dates: list[str], daily):
    panel = build_panel(dates, daily=daily)
    by_date: dict[str, dict[str, float]] = {}
    rows_by: dict[str, list] = {}
    for r in panel:
        rows_by.setdefault(r.date, []).append(r)
    for d, rows in rows_by.items():
        by_date[d] = compose_alpha(rows)
    return by_date


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", default=None, help="默认 $QUANT_HOME/reports/wf")
    ap.add_argument("--train-months", type=int, default=24)
    ap.add_argument("--test-months", type=int, default=6)
    ap.add_argument("--max-positions", type=int, default=10)
    args = ap.parse_args()

    dates = [
        to_iso(d)
        for d in trading_day_list(date.fromisoformat(args.start), date.fromisoformat(args.end))
    ]
    if len(dates) < 300:
        print(f"交易日过少 ({len(dates)})，无法 walk-forward")
        return

    if not args.out:
        from quant.store.paths import reports_dir

        args.out = str(reports_dir("wf"))

    daily = load_adjusted_daily()
    print("构建全区间因子面板…")
    alpha_all = _build_alpha(dates, daily)

    train_size = int(args.train_months * 21)
    test_size = int(args.test_months * 21)
    step = test_size

    grid = {
        "n_enter": [6, 8, 10],
        "n_exit": [12, 15, 18],
        "target_vol": [0.12, 0.15, 0.18],
    }
    n_trials = parameter_budget(3, 3)
    print(f"网格组合数≈{n_trials}")

    best_params_by_fold: list[dict] = []
    tried_sharpes: list[float] = []

    def run_fn(train_dates: list[str], test_dates: list[str]) -> dict:
        best_sh = -1e9
        best_p = {"n_enter": 8, "n_exit": 15, "target_vol": 0.15}
        for ne, nx, tv in itertools.product(grid["n_enter"], grid["n_exit"], grid["target_vol"]):
            if nx < ne:
                continue

            def alpha_fn(d, _rows, _a=alpha_all):
                return _a.get(d, {})

            policy = TargetPortfolio(
                n_enter=ne, n_exit=nx, max_stocks=args.max_positions, target_vol=tv, daily=daily
            )
            broker = run_backtest(
                daily=daily,
                dates=train_dates,
                alpha_fn=alpha_fn,
                policy=policy,
                max_positions=args.max_positions,
                exit_config=ExitConfig(),
                strict_signals=True,
            )
            m = compute_metrics(broker)
            sh = float(m.get("sharpe") or 0.0)
            tried_sharpes.append(sh)
            if sh > best_sh:
                best_sh = sh
                best_p = {"n_enter": ne, "n_exit": nx, "target_vol": tv}

        best_params_by_fold.append(best_p)

        def alpha_fn_test(d, _rows, _a=alpha_all):
            return _a.get(d, {})

        policy = TargetPortfolio(
            n_enter=best_p["n_enter"],
            n_exit=best_p["n_exit"],
            max_stocks=args.max_positions,
            target_vol=best_p["target_vol"],
            daily=daily,
        )
        broker = run_backtest(
            daily=daily,
            dates=test_dates,
            alpha_fn=alpha_fn_test,
            policy=policy,
            max_positions=args.max_positions,
            exit_config=ExitConfig(),
            strict_signals=True,
        )
        eq = [v for _, v in broker.equity_curve]
        return {"equity": eq, "metrics": compute_metrics(broker), "params": best_p}

    wf = walk_forward(
        dates,
        train_size=train_size,
        test_size=test_size,
        step=step,
        run_fn=run_fn,
    )

    def alpha_fn_all(d, _rows):
        return alpha_all.get(d, {})

    is_broker = run_backtest(
        daily=daily,
        dates=dates,
        alpha_fn=alpha_fn_all,
        policy=TargetPortfolio(max_stocks=args.max_positions, daily=daily),
        max_positions=args.max_positions,
        exit_config=ExitConfig(),
        strict_signals=True,
    )
    is_m = compute_metrics(is_broker)
    is_sh = float(is_m.get("sharpe") or 0.0)
    oos_is_ratio = (wf.oos_sharpe / is_sh) if abs(is_sh) > 1e-9 else 0.0

    dsr = deflated_sharpe(
        wf.oos_sharpe,
        n=max(wf.n_test_days, 2),
        n_trials=max(n_trials, len(tried_sharpes) or 1),
    )

    window = dates[-min(252, len(dates)) :]

    def sens_run(val: float) -> float:
        ne = max(3, int(round(val)))

        def af(d, _r):
            return alpha_all.get(d, {})

        b = run_backtest(
            daily=daily,
            dates=window,
            alpha_fn=af,
            policy=TargetPortfolio(
                n_enter=ne, n_exit=max(ne + 5, 15), max_stocks=args.max_positions, daily=daily
            ),
            max_positions=args.max_positions,
            exit_config=ExitConfig(),
            strict_signals=True,
        )
        return float(compute_metrics(b).get("sharpe") or 0.0)

    sens = scan_param("n_enter", [6.0, 8.0, 10.0, 12.0], run_fn=sens_run)

    out = {
        "oos_sharpe": wf.oos_sharpe,
        "oos_ann_return": wf.oos_ann_return,
        "oos_ann_vol": wf.oos_ann_vol,
        "n_folds": wf.n_folds,
        "fold_sharpe": wf.fold_sharpe,
        "is_sharpe": is_sh,
        "oos_is_ratio": round(oos_is_ratio, 3),
        "dsr": dsr,
        "n_trials": n_trials,
        "best_params_by_fold": best_params_by_fold,
        "sensitivity_n_enter": {
            "param": sens.param,
            "grid": sens.grid,
            "sharpes": sens.sharpes,
            "stability": sens.stability,
            "peak_to_median": sens.peak_to_median,
        },
        "parameter_budget_trials": n_trials,
    }
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "walk_forward.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"写入 {path}")


if __name__ == "__main__":
    main()
