#!/usr/bin/env python3
"""抓取同花顺 F10 个股页「概念贴合度排名」列表。

示例:
    python scripts/fetch_ths_concept_fit_rank.py 000636
    python scripts/fetch_ths_concept_fit_rank.py 000636 --json
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx

from quant.data.sources.ths.concept_fit_rank import get_concept_fit_rank_list, normalize_symbol


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="抓取同花顺 F10 概念贴合度排名")
    parser.add_argument("symbol", help="6 位股票代码，如 000636")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args(argv)

    try:
        rows = get_concept_fit_rank_list(args.symbol)
    except (httpx.HTTPError, ValueError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(f"股票 {normalize_symbol(args.symbol)} 概念贴合度排名（共 {len(rows)} 个）:")
        for row in rows:
            print(f"{row['rank']:>2}. {row['concept']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
