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


def fit_walk_forward_weights(
    dates: list[str],
    daily,
    *,
    train_window: int = 504,
    step: int = 63,
    min_icir: float = 0.0,
    min_tstat: float = 1.0,
    fdr_alpha: float = 0.05,
) -> dict[str, dict[str, float]]:
    """滚动 walk-forward：每个 test 日 t 用 [t-train_window, t-1] 面板拟合权重。

    返回 {date: {factor: weight}}。train_window 默认 2 年(504 交易日)，step 默认季度(63)。
    生产 ``daily`` 经 ``load_factor_weights(as_of=t)`` 取 ≤t 最近一组（严格 OOS，
    权重只用 t-1 及更早数据拟合，杜绝 in-sample 高估）。
    """
    from collections import defaultdict

    names = REGISTRY.names()
    panel = build_panel(dates, daily=daily)
    by_date: dict[str, list] = defaultdict(list)
    for r in panel:
        by_date[r.date].append(r)
    sorted_dates = sorted(by_date.keys())
    ts: dict[str, dict[str, float]] = {}
    for i, td in enumerate(sorted_dates):
        if i < train_window or i % step:
            continue
        train_rows: list = []
        for sd in sorted_dates[max(0, i - train_window):i]:
            train_rows.extend(by_date[sd])
        if len(train_rows) < 200:
            continue
        report = factor_ic_report(train_rows, names)
        full = full_weight_map(report, names, min_icir=min_icir, min_tstat=min_tstat, fdr_alpha=fdr_alpha)
        ts[td] = {k: round(v, 4) for k, v in full.items()}
    return ts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--min-icir", type=float, default=0.0)
    ap.add_argument("--min-tstat", type=float, default=1.0)
    ap.add_argument("--walk-forward", action="store_true", help="滚动 walk-forward 时变权重表")
    ap.add_argument("--train-window", type=int, default=504)
    ap.add_argument("--step", type=int, default=63)
    ap.add_argument("--fdr-alpha", type=float, default=0.05, help="BH-FDR 显著性水平（0=关闭）")
    args = ap.parse_args()

    dates = [
        to_iso(d)
        for d in trading_day_list(date.fromisoformat(args.start), date.fromisoformat(args.end))
    ]
    if not dates:
        print("无交易日")
        return

    daily = load_adjusted_daily()
    if args.walk_forward:
        ts = fit_walk_forward_weights(
            dates, daily, train_window=args.train_window, step=args.step,
            min_icir=args.min_icir, min_tstat=args.min_tstat,
        )
        path = config_file("factor_weights_ts.yml")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump({"apply": True, "weights_ts": ts}, f, allow_unicode=True, sort_keys=False)
        print(f"walk-forward 权重表 → {path} | {len(ts)} 个日期")
        return
    panel = build_panel(dates, daily=daily)
    print(f"面板 {len(panel)} 行")
    names = REGISTRY.names()
    report = factor_ic_report(panel, names)
    full = full_weight_map(report, names, min_icir=args.min_icir, min_tstat=args.min_tstat, fdr_alpha=args.fdr_alpha or None)
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
