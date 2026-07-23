"""回测 CLI 入口（R2）。"""

from __future__ import annotations

import argparse
import json

from quant.backtest.broker import BrokerConfig
from quant.r2.backtest.engine import run_r2_backtest


def main() -> None:
    parser = argparse.ArgumentParser(description="GoldQuant R2 历史回测")
    parser.add_argument("--from", dest="from_date", default=None)
    parser.add_argument("--to", dest="to_date", default=None)
    parser.add_argument("--cash", type=float, default=500_000.0)
    parser.add_argument("--ml-mode", default="gate", choices=("shadow", "gate", "rank"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    cfg = BrokerConfig(initial_cash=args.cash)
    result = run_r2_backtest(
        from_date=args.from_date,
        to_date=args.to_date,
        broker_cfg=cfg,
        ml_mode=args.ml_mode,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print("=== GoldQuant R2 回测报告 ===")
    for k, v in result.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
