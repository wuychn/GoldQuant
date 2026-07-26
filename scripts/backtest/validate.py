"""回测正确性检验：随机 alpha 基准 + 因子前视泄漏。

用法：
    python -m scripts.backtest.validate --start 2023-01-01 --end 2024-06-30
"""

from __future__ import annotations

import argparse
from datetime import date

import numpy as np

from quant.backtest2.engine import ExitConfig, run_backtest
from quant.backtest2.metrics import compute_metrics
from quant.data.calendar import to_iso, trading_day_list
from quant.data.store import read_daily_raw
from quant.factors.compose import compose_alpha
from quant.factors.panel_builder import build_panel
from quant.portfolio2.target import TargetPortfolio


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--max-positions", type=int, default=10)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    dates = [
        to_iso(d)
        for d in trading_day_list(date.fromisoformat(args.start), date.fromisoformat(args.end))
    ]
    if len(dates) < 40:
        print("交易日过少")
        return

    daily = read_daily_raw()
    panel = build_panel(dates, daily=daily)
    alpha_by_date: dict[str, dict[str, float]] = {}
    rows_by: dict[str, list] = {}
    for r in panel:
        rows_by.setdefault(r.date, []).append(r)
    for d, rows in rows_by.items():
        alpha_by_date[d] = compose_alpha(rows)

    codes = sorted({r.code for r in panel})
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


if __name__ == "__main__":
    main()
