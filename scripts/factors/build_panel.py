"""构建因子面板脚本。

用法：
    python -m scripts.factors.build_panel --start 2022-01-01 --end 2024-12-31 --out data/panel.parquet
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from quant.data.calendar import to_iso, trading_day_list
from quant.factors.panel_builder import build_panel


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--out", default="data/panel.parquet")
    args = ap.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    dates = [to_iso(d) for d in trading_day_list(start, end)]
    if not dates:
        print("无交易日")
        return
    rows = build_panel(dates)
    print(f"构建 {len(rows)} 行")

    # 序列化为 records
    records = []
    for r in rows:
        rec = {
            "date": r.date,
            "code": r.code,
            "name": r.name,
            "industry": r.industry,
            "log_mcap": r.log_mcap,
            "forward_return_pct": r.forward_return_pct,
            "raw": json.dumps(r.raw, ensure_ascii=False),
            "neutral": json.dumps(r.neutral, ensure_ascii=False),
            # 保留多档前瞻收益，供存盘 parquet 的 IC decay 使用（此前丢弃致 decay 全 0）
            "fwd": json.dumps(r.meta.get("fwd", {}), ensure_ascii=False),
        }
        records.append(rec)

    try:
        import pandas as pd

        df = pd.DataFrame(records)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(args.out, index=False)
        print(f"已写入 {args.out}")
    except ImportError:
        print("pyarrow 未安装，仅打印前 5 行")
        for r in records[:5]:
            print(r)


if __name__ == "__main__":
    main()
