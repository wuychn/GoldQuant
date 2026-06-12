"""回测 CLI 入口。"""

from __future__ import annotations

import argparse
import json

from quant.backtest.broker import BrokerConfig
from quant.backtest.engine import run_backtest


def main() -> None:
    parser = argparse.ArgumentParser(description="GoldQuant 历史回测（重放 ~/.quant/daily 盘中快照）")
    parser.add_argument("--from", dest="from_date", default=None, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", default=None, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--cash", type=float, default=100_000.0, help="初始资金")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    cfg = BrokerConfig(initial_cash=args.cash)
    result = run_backtest(from_date=args.from_date, to_date=args.to_date, broker_cfg=cfg)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print("=== GoldQuant 回测报告 ===")
    for k, v in result.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
