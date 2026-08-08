"""构建因子面板脚本。

用法：
    python -m scripts.factors.build_panel --start 2022-01-01 --end 2024-12-31 --out data/panel.parquet
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from common.progress_log import log_progress_done, log_progress_error, log_progress_start
from quant.data.calendar import to_iso, trading_day_list
from quant.factors.panel_builder import build_panel
from quant.store.paths import reports_dir
from scripts.cli_home import add_home_argument, home_context

_SCOPE = "build_panel"


def main() -> None:
    ap = argparse.ArgumentParser(description="构建因子面板并写入 parquet")
    add_home_argument(ap)
    ap.add_argument("--start", required=True, help="面板起始日 YYYY-MM-DD（含）")
    ap.add_argument("--end", required=True, help="面板结束日 YYYY-MM-DD（含）")
    ap.add_argument(
        "--out",
        default=None,
        help="输出 parquet 路径；默认 $QUANT_HOME/reports/panel/panel.parquet",
    )
    args = ap.parse_args()

    with home_context(args.home):
        out_path = args.out or str(reports_dir("panel") / "panel.parquet")
        log_progress_start(_SCOPE, "开始", detail=f"{args.start} ~ {args.end}")
        try:
            start = date.fromisoformat(args.start)
            end = date.fromisoformat(args.end)
            dates = [to_iso(d) for d in trading_day_list(start, end)]
            if not dates:
                log_progress_error(_SCOPE, "失败", detail="无交易日")
                sys.exit(1)
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
                Path(out_path).parent.mkdir(parents=True, exist_ok=True)
                df.to_parquet(out_path, index=False)
                print(f"已写入 {out_path}")
                log_progress_done(_SCOPE, "成功", detail=f"{len(rows)} 行 → {out_path}")
            except ImportError:
                print("pyarrow 未安装，仅打印前 5 行")
                for r in records[:5]:
                    print(r)
                log_progress_done(_SCOPE, "成功", detail=f"{len(rows)} 行（未写 parquet）")
        except SystemExit:
            raise
        except Exception as e:
            log_progress_error(_SCOPE, "失败", detail=f"{type(e).__name__}: {e}")
            raise


if __name__ == "__main__":
    main()
