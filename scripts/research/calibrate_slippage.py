"""滑点经验校准（Phase 7）：用纸面成交记录反推真实滑点，校准 quant.yml 配置。

成交记录含 ``信号价``（滑点前）与 ``成交价``（滑点后）。本脚本聚合历史成交，
按方向计算 |成交价/信号价 - 1| 的均值/中位数，与 ``gates.trading.simulation.slippage_pct``
对比，给出调参建议。

用法::

    python -m scripts.research.calibrate_slippage
    python -m scripts.research.calibrate_slippage --days 60
"""

from __future__ import annotations

import argparse
import statistics

from common.progress_log import log_progress_done, log_progress_error, log_progress_start
from quant.store.paths import quant_home

_SCOPE = "calibrate_slippage"


def _iter_executed_files(days: int):
    import json
    from datetime import date, timedelta

    root = quant_home() / "daily"
    if not root.is_dir():
        return
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    for d in sorted(root.glob("[0-9]" * 4 + "*"), reverse=True):
        name = d.name
        if name < cutoff:
            break
        path = d / "trades" / "executed.json"
        if path.is_file():
            try:
                rows = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            for r in rows or []:
                if isinstance(r, dict):
                    yield r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    args = ap.parse_args()

    log_progress_start(_SCOPE, "开始", detail=f"days={args.days}")
    try:
        by_side: dict[str, list[float]] = {"买入": [], "卖出": []}
        for r in _iter_executed_files(args.days):
            sig = r.get("信号价")
            fill = r.get("成交价")
            side = r.get("方向")
            try:
                sig = float(sig)
                fill = float(fill)
            except (TypeError, ValueError):
                continue
            if not sig or sig <= 0 or side not in by_side:
                continue
            by_side[side].append(abs(fill / sig - 1.0))

        from quant.config import load_gates_config

        cfg_slip = float((load_gates_config().get("trading") or {}).get("simulation", {}).get("slippage_pct", 0.001))

        print(f"窗口: 近 {args.days} 天 | 配置 slippage_pct={cfg_slip:.4f} ({cfg_slip*100:.2f}%)\n")
        for side, vals in by_side.items():
            if not vals:
                print(f"{side}: 无成交记录（含信号价）")
                continue
            mean = statistics.mean(vals)
            med = statistics.median(vals)
            p95 = sorted(vals)[int(len(vals) * 0.95)] if len(vals) >= 20 else max(vals)
            print(f"{side}: n={len(vals)} 均值={mean*100:.3f}% 中位={med*100:.3f}% p95={p95*100:.3f}%")
            if mean > cfg_slip * 1.5:
                print(f"  ⚠️ 实测滑点显著高于配置（{mean/cfg_slip:.1f}×）→ 建议上调 slippage_pct")
            elif mean < cfg_slip * 0.5:
                print(f"  ℹ️ 实测滑点低于配置 → 可下调（或维持保守）")
        n = sum(len(v) for v in by_side.values())
        log_progress_done(_SCOPE, "成功", detail=f"n={n}")
    except Exception as e:
        log_progress_error(_SCOPE, "失败", detail=f"{type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    main()
