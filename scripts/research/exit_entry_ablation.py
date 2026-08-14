"""出场/入场/仓位/熔断对照实验。

用法::

    poetry run python -m scripts.research.exit_entry_ablation \\
        --home D:/ProgramData/.quant --start 2021-07-01 --end 2022-06-30 --workers 4 \\
        --only mom10,mom15_tv08,mom15_fi50,mom15_halt,combo
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
from datetime import date
from pathlib import Path

from common.progress_log import log_progress, log_progress_done, log_progress_error, log_progress_start
from quant.backtest.engine import DrawdownHaltConfig, ExitConfig, run_backtest
from quant.backtest.metrics import compute_metrics
from quant.backtest.report import export_report
from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import to_iso, trading_day_list
from quant.data.industry import read_industry_snapshot
from quant.factors.alpha_builder import build_alpha_by_date
from quant.portfolio.target import TargetPortfolio
from quant.store.paths import reports_dir
from scripts.cli_home import add_home_argument, home_context

_SCOPE = "research.ablation"


def _variants() -> list[tuple[str, dict]]:
    """变体：exit / 入场过滤 / 仓位 / 熔断。"""
    ex = ExitConfig()
    return [
        ("baseline", {"exit": ex, "max_entry_ret_5d": None}),
        ("mom15", {"exit": ex, "max_entry_ret_5d": 0.15}),
        ("mom10", {"exit": ex, "max_entry_ret_5d": 0.10}),
        (
            "mom15_tv08",
            {"exit": ex, "max_entry_ret_5d": 0.15, "target_vol": 0.08},
        ),
        (
            "mom15_fi50",
            {"exit": ex, "max_entry_ret_5d": 0.15, "full_invest": 0.50},
        ),
        (
            "mom15_halt",
            {
                "exit": ex,
                "max_entry_ret_5d": 0.15,
                "halt": DrawdownHaltConfig(True, 15.0, 5),
            },
        ),
        (
            "mom15_halt10",
            {
                "exit": ex,
                "max_entry_ret_5d": 0.15,
                "halt": DrawdownHaltConfig(True, 10.0, 10),
            },
        ),
        (
            "combo",
            {
                "exit": ex,
                "max_entry_ret_5d": 0.10,
                "target_vol": 0.08,
                "full_invest": 0.60,
                "halt": DrawdownHaltConfig(True, 12.0, 8),
                "max_positions": 6,
                "n_enter": 6,
                "n_exit": 12,
            },
        ),
        (
            "combo_wide",
            {
                "exit": ExitConfig(hard_pct=0.12, atr_mult_stop=3.0),
                "max_entry_ret_5d": 0.10,
                "target_vol": 0.08,
                "full_invest": 0.60,
                "halt": DrawdownHaltConfig(True, 12.0, 8),
                "max_positions": 6,
                "n_enter": 6,
                "n_exit": 12,
            },
        ),
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description="盈利导向：过滤/降仓/熔断对照")
    add_home_argument(ap)
    ap.add_argument("--start", default="2021-07-01")
    ap.add_argument("--end", default="2022-06-30")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-positions", type=int, default=10)
    ap.add_argument("--n-enter", type=int, default=8)
    ap.add_argument("--n-exit", type=int, default=15)
    ap.add_argument("--target-vol", type=float, default=0.15)
    ap.add_argument("--out", default=None)
    ap.add_argument("--alpha-cache", default=None)
    ap.add_argument("--rebuild-alpha", action="store_true")
    ap.add_argument("--only", default=None, help="逗号分隔变体名")
    args = ap.parse_args()

    log_progress_start(_SCOPE, "开始", detail=f"{args.start}~{args.end} workers={args.workers}")
    try:
        with home_context(args.home):
            out_root = Path(args.out or reports_dir("bt_ablation"))
            out_root.mkdir(parents=True, exist_ok=True)
            dates = [
                to_iso(d)
                for d in trading_day_list(date.fromisoformat(args.start), date.fromisoformat(args.end))
            ]
            if not dates:
                log_progress_error(_SCOPE, "失败", detail="无交易日")
                sys.exit(1)

            t0 = time.perf_counter()
            daily = load_adjusted_daily()
            cache = Path(args.alpha_cache or (out_root / "alpha_by_date.pkl"))
            if cache.is_file() and not args.rebuild_alpha:
                log_progress(_SCOPE, "加载 alpha 缓存", detail=str(cache))
                with cache.open("rb") as f:
                    alpha_by_date = pickle.load(f)
            else:
                log_progress(_SCOPE, "构建 alpha …", detail=f"workers={args.workers}")
                t_a = time.perf_counter()
                alpha_by_date = build_alpha_by_date(
                    dates, daily, use_ic_weights=True, workers=args.workers
                )
                with cache.open("wb") as f:
                    pickle.dump(alpha_by_date, f, protocol=pickle.HIGHEST_PROTOCOL)
                print(f"alpha {len(alpha_by_date)} 日 · {time.perf_counter()-t_a:.1f}s → {cache}", flush=True)

            def alpha_fn(d: str, _rows: dict) -> dict[str, float]:
                return alpha_by_date.get(d, {})

            sectors = read_industry_snapshot(dates[-1]) if dates else {}
            summary: list[dict] = []
            keys = [
                "total_return_pct",
                "ann_return_pct",
                "sharpe",
                "max_drawdown_pct",
                "win_rate",
                "n_trades",
                "final_equity",
            ]

            variants = _variants()
            if args.only:
                want = {x.strip() for x in args.only.split(",") if x.strip()}
                variants = [v for v in variants if v[0] in want]
                if not variants:
                    log_progress_error(_SCOPE, "失败", detail=f"--only 无匹配: {args.only}")
                    sys.exit(1)

            for name, cfg in variants:
                log_progress(_SCOPE, f"变体 {name}", detail="跑回测")
                t_b = time.perf_counter()
                max_pos = int(cfg.get("max_positions", args.max_positions))
                n_enter = int(cfg.get("n_enter", args.n_enter))
                n_exit = int(cfg.get("n_exit", args.n_exit))
                policy = TargetPortfolio.from_config(
                    n_enter=n_enter,
                    n_exit=n_exit,
                    max_stocks=max_pos,
                    target_vol=float(cfg.get("target_vol", args.target_vol)),
                    full_invest=float(cfg.get("full_invest", 0.95)),
                    daily=daily,
                    sectors=sectors,
                    max_entry_ret_5d=cfg.get("max_entry_ret_5d"),
                )
                broker = run_backtest(
                    daily=daily,
                    dates=dates,
                    alpha_fn=alpha_fn,
                    policy=policy,
                    max_positions=max_pos,
                    exit_config=cfg["exit"],
                    strict_signals=True,
                    drawdown_halt=cfg.get("halt"),
                )
                m = compute_metrics(broker, daily=None)
                ea = m.get("exit_attribution") or {}
                row = {k: m.get(k) for k in keys}
                row["variant"] = name
                row["bt_sec"] = round(time.perf_counter() - t_b, 1)
                row["hard_stop_pnl"] = (ea.get("hard_stop") or {}).get("total_pnl")
                row["rebalance_pnl"] = (ea.get("rebalance") or {}).get("total_pnl")
                summary.append(row)
                export_report(broker, str(out_root / name), daily=None, strict_signals=True)
                print(
                    f"[{name}] ret={row['total_return_pct']} sharpe={row['sharpe']} "
                    f"mdd={row['max_drawdown_pct']} eq={row['final_equity']} "
                    f"hs_pnl={row['hard_stop_pnl']} reb_pnl={row['rebalance_pnl']} "
                    f"({row['bt_sec']}s)",
                    flush=True,
                )

            summary_path = out_root / "summary_profit.json"
            summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
            lines = [
                f"# Profit ablation {args.start} ~ {args.end}",
                "",
                "| variant | total_ret% | sharpe | mdd% | final_eq | hard_stop_pnl | rebalance_pnl |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
            for r in summary:
                lines.append(
                    f"| {r['variant']} | {r['total_return_pct']} | {r['sharpe']} | "
                    f"{r['max_drawdown_pct']} | {r['final_equity']} | "
                    f"{r['hard_stop_pnl']} | {r['rebalance_pnl']} |"
                )
            (out_root / "summary_profit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
            print("\n".join(lines), flush=True)
            log_progress_done(
                _SCOPE,
                "成功",
                detail=f"{summary_path} · 总耗时 {time.perf_counter()-t0:.1f}s",
            )
    except SystemExit:
        raise
    except Exception as e:
        log_progress_error(_SCOPE, "失败", detail=f"{type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    main()
