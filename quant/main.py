#!/usr/bin/env python3
"""A 股短线量化机器人 CLI 入口。

用法::

    python -m quant pre_market
    python -m quant during_market
    python -m quant post_market_lunch
    python -m quant post_market_evening
    python -m quant news

ML 校准（独立命令）::

    python -m quant.ml calibrate --method grid --apply
"""

import os
import sys
from datetime import datetime

for k in list(os.environ.keys()):
    if "proxy" in k.lower():
        del os.environ[k]

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from quant.orchestrator import run_mode


def main():
    if len(sys.argv) < 2:
        print("用法: python -m quant <mode>")
        print("可用模式: news | pre_market | during_market | post_market_lunch | post_market_evening")
        sys.exit(1)
    mode = sys.argv[1]
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run_mode(mode, timestamp)


if __name__ == "__main__":
    main()
