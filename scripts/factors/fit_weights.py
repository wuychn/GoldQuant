"""离线拟合 IC 驱动权重 → ``~/.quant/config/factor_weights.yml``。

在历史区间上跑因子面板 + IC 报告，按 ICIR 产出权重。仅保留 IC 显著为正的因子，
其余置 0。供 live 日决策（``daily.py`` 经 ``load_factor_weights``）使用——
"过去拟合、今日应用"即干净的 OOS。

用法::

    python -m scripts.factors.fit_weights --start 2023-01-01 --end 2024-12-31
    python -m scripts.factors.fit_weights --start 2023-01-01 --end 2024-12-31 --min-tstat 1.5
"""

from __future__ import annotations

import argparse
from datetime import date

import yaml

from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import to_iso, trading_day_list
from quant.factors.ic import factor_ic_report
from quant.factors.panel_builder import build_panel
from quant.factors.registry import REGISTRY
from quant.factors.weights import full_weight_map
from quant.store.paths import config_file


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--min-icir", type=float, default=0.0)
    ap.add_argument("--min-tstat", type=float, default=1.0)
    args = ap.parse_args()

    dates = [
        to_iso(d)
        for d in trading_day_list(date.fromisoformat(args.start), date.fromisoformat(args.end))
    ]
    if not dates:
        print("无交易日")
        return

    daily = load_adjusted_daily()
    panel = build_panel(dates, daily=daily)
    print(f"面板 {len(panel)} 行")
    names = REGISTRY.names()
    report = factor_ic_report(panel, names)
    full = full_weight_map(report, names, min_icir=args.min_icir, min_tstat=args.min_tstat)
    weights = {k: round(v, 4) for k, v in full.items()}
    n_pos = sum(1 for v in weights.values() if v > 0)

    out = {
        "apply": True,
        "weights": weights,
        "meta": {
            n: {
                "ic_mean": round(float(report.get(n, {}).get("ic_mean") or 0.0), 4),
                "icir": round(float(report.get(n, {}).get("icir") or 0.0), 4),
                "t_stat": round(float(report.get(n, {}).get("t_stat") or 0.0), 4),
            }
            for n in names
        },
    }
    path = config_file("factor_weights.yml")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(out, f, allow_unicode=True, sort_keys=False)
    print(f"写入 {path} | 入选 {n_pos}/{len(names)} 因子")


if __name__ == "__main__":
    main()
