#!/usr/bin/env python3
"""A 股短线量化机器人 CLI 入口。

用法::

    python -m quant pre_market
    python -m quant during_market
    python -m quant post_market_lunch
    python -m quant post_market_evening
    python -m quant news
    python -m quant prefetch_concepts

ML 校准（独立命令）::

    python -m quant.ml calibrate --method grid --apply
"""

import os
import sys

for k in list(os.environ.keys()):
    if "proxy" in k.lower():
        del os.environ[k]

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from quant.orchestrator import run_mode
from quant.progress_log import configure_progress_logging
from quant.timeutil import cn_datetime_str


def main():
    configure_progress_logging()
    if len(sys.argv) < 2:
        print("用法: python -m quant <mode>")
        print(
            "可用模式: news | pre_market | during_market | post_market_lunch | "
            "post_market_evening | prefetch_concepts"
        )
        sys.exit(1)
    mode = sys.argv[1]
    if mode == "prefetch_concepts":
        import asyncio

        from app.services.stock_concept_cache import prefetch_optional_holding_concepts
        from app.services.stock_jbxx_cache import prefetch_optional_holding_jbxx

        n_concepts = asyncio.run(prefetch_optional_holding_concepts())
        n_jbxx = prefetch_optional_holding_jbxx()
        print(f"预取完成：问财概念新拉取 {n_concepts} 只，基本信息新拉取 {n_jbxx} 只")
        return
    timestamp = cn_datetime_str()
    run_mode(mode, timestamp)


if __name__ == "__main__":
    main()
