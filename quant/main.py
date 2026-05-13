#!/usr/bin/env python3
"""A股短线交易机器人 — 模块化入口。"""

import os
import sys
from datetime import datetime

# 禁用代理
for k in list(os.environ.keys()):
    if "proxy" in k.lower():
        del os.environ[k]

from quant.orchestrator import run_mode


def main():
    if len(sys.argv) < 2:
        print("用法: python main.py <mode>")
        print("可用模式: news | pre_market | during_market | post_market_lunch | post_market_evening")
        sys.exit(1)
    mode = sys.argv[1]
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run_mode(mode, timestamp)


if __name__ == "__main__":
    main()
