#!/usr/bin/env python3
"""A 股短线量化机器人 CLI（R2）。"""

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


def main():
    configure_progress_logging()
    if len(sys.argv) < 2:
        print("用法: python -m quant <mode>")
        print(
            "可用: news | pre_market | during_market | post_market_lunch | "
            "post_market_evening | prefetch_concepts | industry_aliases_draft | "
            "r2_validate | r2_ml_etl | r2_ml_train"
        )
        sys.exit(1)
    mode = sys.argv[1]
    if mode == "industry_aliases_draft":
        from quant.scoring.industry_aliases_draft import main as industry_aliases_draft_main

        industry_aliases_draft_main()
        return
    if mode == "prefetch_concepts":
        import asyncio

        from app.services.stock_concept_cache import prefetch_optional_holding_concepts
        from app.services.stock_jbxx_cache import prefetch_optional_holding_jbxx

        n_concepts = asyncio.run(prefetch_optional_holding_concepts())
        n_jbxx = prefetch_optional_holding_jbxx()
        print(f"预取完成：问财概念 {n_concepts} 只，基本信息 {n_jbxx} 只")
        return
    if mode == "r2_validate":
        from quant.r2.backtest.validate import run_validation

        fr = sys.argv[2] if len(sys.argv) > 2 else None
        to = sys.argv[3] if len(sys.argv) > 3 else None
        print(run_validation(from_date=fr, to_date=to))
        return
    if mode == "r2_ml_etl":
        from quant.r2.ml.etl import run_etl

        fr = sys.argv[2] if len(sys.argv) > 2 else None
        to = sys.argv[3] if len(sys.argv) > 3 else None
        print(run_etl(from_date=fr, to_date=to))
        return
    if mode == "r2_ml_train":
        from quant.r2.ml.train import train_sector_fade, train_stock_rank

        print(train_stock_rank())
        print(train_sector_fade())
        return
    from quant.r2.orchestrator import run_mode

    run_mode(mode, cn_datetime_str())


if __name__ == "__main__":
    main()
