#!/usr/bin/env python3
"""GoldQuant r3 CLI。

用法::

    python -m quant news
    python -m quant pre_market
    python -m quant during_market
    python -m quant post_market_lunch
    python -m quant post_market_evening
    python -m quant daily_decision
    python -m quant prefetch_concepts
"""

from __future__ import annotations

import os
import sys

for k in list(os.environ.keys()):
    if "proxy" in k.lower():
        del os.environ[k]

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from quant.progress_log import configure_progress_logging
from quant.timeutil import cn_datetime_str


def main() -> None:
    configure_progress_logging()
    if len(sys.argv) < 2:
        print("用法: python -m quant <mode>")
        print(
            "可用: news | pre_market | during_market | post_market_lunch | "
            "post_market_evening | daily_decision | prefetch_concepts"
        )
        sys.exit(1)
    mode = sys.argv[1]
    if mode == "prefetch_concepts":
        import asyncio

        from app.services.stock_concept_cache import prefetch_optional_holding_concepts
        from app.services.stock_jbxx_cache import prefetch_optional_holding_jbxx

        n_concepts = asyncio.run(prefetch_optional_holding_concepts())
        n_jbxx = prefetch_optional_holding_jbxx()
        print(f"预取完成：概念 {n_concepts} 只，基本信息 {n_jbxx} 只")
        return
    if mode == "daily_decision":
        from quant.ops.runner import run_daily_decision

        dry = "--dry-run" in sys.argv
        no_push = "--no-push" in sys.argv
        run_daily_decision(push=not no_push, dry_run=dry)
        return

    from quant.ops.runner import run_mode

    no_push = "--no-push" in sys.argv
    run_mode(mode, cn_datetime_str(), push=not no_push)


if __name__ == "__main__":
    main()
