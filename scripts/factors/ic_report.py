"""因子 IC 报告脚本。

用法：
    python -m scripts.factors.ic_report --panel data/panel.parquet --out reports/ic.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from quant.factors.ic import factor_ic_report
from quant.factors.registry import REGISTRY


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", required=True)
    ap.add_argument("--out", default="reports/ic.json")
    args = ap.parse_args()

    try:
        import pandas as pd

        df = pd.read_parquet(args.panel)
    except ImportError:
        print("需要 pyarrow")
        return

    from quant.factors.base import FactorRow

    rows: list[FactorRow] = []
    for _, r in df.iterrows():
        rows.append(
            FactorRow(
                date=str(r["date"]),
                code=str(r["code"]),
                name=str(r.get("name", "")),
                industry=str(r.get("industry", "")),
                log_mcap=float(r["log_mcap"]) if r.get("log_mcap") is not None else None,
                raw=json.loads(r["raw"]) if isinstance(r["raw"], str) else dict(r["raw"]),
                neutral=json.loads(r["neutral"]) if isinstance(r["neutral"], str) else dict(r["neutral"]),
                forward_return_pct=float(r["forward_return_pct"]) if r.get("forward_return_pct") is not None else None,
            )
        )

    report = factor_ic_report(rows, REGISTRY.names())
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
