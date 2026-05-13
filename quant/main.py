#!/usr/bin/env python3
"""A股短线交易机器人 — 模块化入口。"""

import os
import sys
from datetime import datetime

# 禁用代理
for k in list(os.environ.keys()):
    if "proxy" in k.lower():
        del os.environ[k]

# 直接执行本文件时，脚本目录在 sys.path 首位，无法解析顶层的 quant 包；
# 将项目根目录插入 path，与 python -m quant 行为一致。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

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
