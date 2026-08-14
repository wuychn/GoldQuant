"""反转合成 alpha 短窗验证：若 RankIC 显著为负，取负应翻正边。

用法::

    poetry run python -m scripts.research.verify_invert_alpha \\
        --home D:/ProgramData/.quant \\
        --alpha-cache D:/ProgramData/.quant/reports/bt_profit_calm/alpha_by_date.pkl \\
        --start 2023-01-01 --end 2025-12-31
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
from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import to_iso, trading_day_list
from quant.data.industry import read_industry_snapshot
from quant.portfolio.target import TargetPortfolio
from scripts.cli_home import add_home_argument, home_context

_SCOPE = "verify_invert_alpha"


def main() -> None:
    ap = argparse.ArgumentParser()
    add_home_argument(ap)
    ap.add_argument("--alpha-cache", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    log_progress_start(_SCOPE, "开始", detail=f"{args.start}~{args.end}")
    try:
        with home_context(args.home):
            with open(args.alpha_cache, "rb") as f:
                alpha_by_date = pickle.load(f)
            daily = load_adjusted_daily()
            dates = [
                to_iso(d)
                for d in trading_day_list(date.fromisoformat(args.start), date.fromisoformat(args.end))
            ]
            sectors = read_industry_snapshot(dates[-1]) if dates else {}

            def make_fn(invert: bool):
                def fn(d, _rows):
                    a = alpha_by_date.get(d, {})
                    if not invert:
                        return a
                    return {c: -float(v) for c, v in a.items()}
                return fn

            halt = DrawdownHaltConfig(True, 10.0, 10)
            rows = []
            for name, invert in [("raw", False), ("invert", True)]:
                log_progress(_SCOPE, f"回测 {name}")
                t0 = time.perf_counter()
                pol = TargetPortfolio.from_config(
                    n_enter=8,
                    n_exit=15,
                    max_stocks=10,
                    target_vol=0.15,
                    daily=daily,
                    sectors=sectors,
                    max_entry_ret_5d=0.15,
                )
                b = run_backtest(
                    daily=daily,
                    dates=dates,
                    alpha_fn=make_fn(invert),
                    policy=pol,
                    max_positions=10,
                    exit_config=ExitConfig(),
                    strict_signals=True,
                    drawdown_halt=halt,
                )
                m = compute_metrics(b, daily=None)
                row = {
                    "variant": name,
                    "ret": m.get("total_return_pct"),
                    "sharpe": m.get("sharpe"),
                    "mdd": m.get("max_drawdown_pct"),
                    "eq": m.get("final_equity"),
                    "sec": round(time.perf_counter() - t0, 1),
                }
                rows.append(row)
                print(row, flush=True)

            out = Path(args.out or Path(args.alpha_cache).parent / "invert_verify.json")
            out.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
            log_progress_done(_SCOPE, "成功", detail=str(out))
    except SystemExit:
        raise
    except Exception as e:
        log_progress_error(_SCOPE, "失败", detail=f"{type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    main()
