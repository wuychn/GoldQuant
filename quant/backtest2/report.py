"""回测报告导出：权益曲线 + 成交明细 CSV/Parquet。"""

from __future__ import annotations

import csv
from pathlib import Path

from quant.backtest2.broker import SimBroker
from quant.backtest2.metrics import compute_metrics


def export_report(broker: SimBroker, out_dir: str) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 权益曲线
    eq_path = out / "equity_curve.csv"
    with open(eq_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "equity"])
        for d, v in broker.equity_curve:
            w.writerow([d, round(v, 2)])

    # 成交明细
    tr_path = out / "trades.csv"
    with open(tr_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "code", "side", "shares", "price", "cost", "pnl"])
        for t in broker.trades:
            w.writerow([t.date, t.code, t.side, t.shares, round(t.price, 4), round(t.cost, 2), round(t.pnl, 2)])

    metrics = compute_metrics(broker)
    mt_path = out / "metrics.json"
    import json

    with open(mt_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    return {"equity_curve": str(eq_path), "trades": str(tr_path), "metrics": str(mt_path), "metrics_data": metrics}
