"""回测正确性检验：随机 alpha 基准 + 因子前视泄漏。

用法：
    python -m scripts.backtest.validate --start 2023-01-01 --end 2024-06-30
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

import numpy as np

from common.progress_log import log_progress_done, log_progress_error, log_progress_start
from quant.backtest.engine import ExitConfig, run_backtest
from quant.backtest.metrics import compute_metrics
from quant.data.calendar import to_iso, trading_day_list
from quant.data.adjust import load_adjusted_daily
from quant.factors.alpha_builder import build_alpha_by_date
from quant.portfolio.target import TargetPortfolio

_SCOPE = "backtest.validate"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--max-positions", type=int, default=10)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    log_progress_start(_SCOPE, "开始", detail=f"{args.start} ~ {args.end}")
    try:
        dates = [
            to_iso(d)
            for d in trading_day_list(date.fromisoformat(args.start), date.fromisoformat(args.end))
        ]
        if len(dates) < 40:
            log_progress_error(_SCOPE, "失败", detail=f"交易日过少 ({len(dates)})")
            sys.exit(1)

        daily = load_adjusted_daily()
        alpha_by_date = build_alpha_by_date(dates, daily, use_ic_weights=True)

        codes = sorted(daily["code"].astype(str).unique())
        rng = np.random.default_rng(args.seed)
        random_alpha: dict[str, dict[str, float]] = {}
        for d in dates:
            random_alpha[d] = {c: float(rng.normal()) for c in codes}

        # 前视：用 T+1 的 alpha 选 T 的票
        lookahead: dict[str, dict[str, float]] = {}
        for i, d in enumerate(dates[:-1]):
            lookahead[d] = alpha_by_date.get(dates[i + 1], {})

        def _run(alpha_map: dict[str, dict[str, float]], label: str):
            def af(d, _r):
                return alpha_map.get(d, {})

            broker = run_backtest(
                daily=daily,
                dates=dates,
                alpha_fn=af,
                policy=TargetPortfolio(max_stocks=args.max_positions, daily=daily),
                max_positions=args.max_positions,
                exit_config=ExitConfig(),
                strict_signals=True,
            )
            m = compute_metrics(broker)
            print(f"[{label}] sharpe={m.get('sharpe')} ret={m.get('total_return_pct')}% trades={m.get('n_trades')}")
            return m

        real = _run(alpha_by_date, "real_alpha")
        rnd = _run(random_alpha, "random_alpha")
        leak = _run(lookahead, "lookahead_alpha")

        print("\n=== 判定 ===")
        if abs(float(rnd.get("sharpe") or 0)) > 1.0 and float(rnd.get("sharpe") or 0) > float(real.get("sharpe") or 0):
            print("WARN: 随机 alpha 显著更好 → 可能有前视泄漏或成本建模问题")
        else:
            print("OK: 随机 alpha 未显著优于真实因子")

        if float(leak.get("sharpe") or 0) > float(real.get("sharpe") or 0) + 0.3:
            print("OK: 前视因子 Sharpe 明显上升 → 因子有信息量")
        else:
            print("WARN: 前视因子未显著提升 → 因子信号弱或实现有问题")
        log_progress_done(_SCOPE, "成功")
    except SystemExit:
        raise
    except Exception as e:
        log_progress_error(_SCOPE, "失败", detail=f"{type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    main()
