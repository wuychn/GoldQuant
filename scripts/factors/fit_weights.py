"""离线拟合 IC 驱动权重 → ``~/.quant/config/factor_weights_ts.yml``（walk-forward，推荐）。

在历史区间上跑因子面板 + IC 报告，按多持有期 ICIR 产出权重。仅保留 IC 显著为正的因子。
live 日决策经 ``load_factor_weights(as_of)`` 取 ≤as_of 最近 walk-forward 权重（严格 OOS）。

用法::

    python -m scripts.factors.fit_weights --start 2023-01-01 --end 2024-12-31 --walk-forward
    python -m scripts.factors.fit_weights --start 2023-01-01 --end 2024-12-31 --walk-forward --min-tstat 1.5
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

import yaml

from common.progress_log import log_progress, log_progress_done, log_progress_error, log_progress_start
from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import to_iso, trading_day_list
from quant.data.universe import build_universe_snapshot
from quant.factors.ic import factor_ic_report
from quant.factors.panel_builder import build_panel
from quant.factors.registry import REGISTRY
from quant.factors.weights import full_weight_map
from quant.store.paths import config_file

_SCOPE = "fit_weights"


def _universe_by_date(dates: list[str], daily) -> dict[str, list[str]]:
    """一次性构建每日 universe（用内存 daily，避免 build_panel 内逐日重读
    parquet + 重算 ADV 的 O(N²) 开销）。"""
    out: dict[str, list[str]] = {}
    for d in dates:
        snap = build_universe_snapshot(d, daily=daily)
        out[d] = list(snap.loc[snap["included"], "code"].astype(str))
    return out


def fit_walk_forward_weights(
    dates: list[str],
    daily,
    *,
    train_window: int = 504,
    step: int = 63,
    min_icir: float = 0.0,
    min_tstat: float = 1.0,
    fdr_alpha: float = 0.05,
    horizon: int = 5,
    horizons: tuple[int, ...] = (5, 10, 20),
) -> dict[str, dict]:
    """滚动 walk-forward：每个 test 日 t 用 [t-train_window, t-1] 面板拟合权重。

    返回 {date: {weights, train_start, train_end}}。
    """
    from collections import defaultdict

    names = REGISTRY.names()
    panel = build_panel(dates, daily=daily, universe_by_date=_universe_by_date(dates, daily))
    by_date: dict[str, list] = defaultdict(list)
    for r in panel:
        by_date[r.date].append(r)
    sorted_dates = sorted(by_date.keys())
    label_horizon = max(horizons) if horizons else horizon
    ts: dict[str, dict] = {}
    fold_i = 0
    for i, td in enumerate(sorted_dates):
        if i < train_window or i % step:
            continue
        train_start_idx = max(0, i - train_window)
        train_end_idx = i - label_horizon  # 按 max(horizons) 丢弃，避免 10/20 日 IC 标签泄漏
        if train_end_idx <= train_start_idx:
            continue
        train_start = sorted_dates[train_start_idx]
        train_end = sorted_dates[train_end_idx - 1]
        train_rows: list = []
        for sd in sorted_dates[train_start_idx:train_end_idx]:
            train_rows.extend(by_date[sd])
        if len(train_rows) < 200:
            continue
        fold_i += 1
        log_progress(
            _SCOPE,
            "walk-forward fold",
            detail=f"#{fold_i} test={td} train={train_start}~{train_end} rows={len(train_rows)}",
        )
        report_list = factor_ic_report(train_rows, names, horizon=horizon, horizons=horizons)
        report = {r["factor"]: r for r in report_list if r.get("factor")}
        full = full_weight_map(
            report, names, min_icir=min_icir, min_tstat=min_tstat, fdr_alpha=fdr_alpha
        )
        ts[td] = {
            "weights": {k: round(v, 4) for k, v in full.items()},
            "train_start": train_start,
            "train_end": train_end,
            "horizon": horizon,
            "horizons": list(horizons),
            "label_horizon": label_horizon,
        }
    return ts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--min-icir", type=float, default=0.0)
    ap.add_argument("--min-tstat", type=float, default=1.0)
    ap.add_argument("--static", action="store_true", help="写静态全样本权重（勿用于 live；默认 walk-forward）")
    ap.add_argument("--train-window", type=int, default=504)
    ap.add_argument("--step", type=int, default=63)
    ap.add_argument("--fdr-alpha", type=float, default=0.05, help="BH-FDR 显著性水平（0=关闭）")
    ap.add_argument("--horizon", type=int, default=5, help="主持有期（日）")
    ap.add_argument("--horizons", default="5,10,20", help="多持有期 ICIR 混合，逗号分隔")
    args = ap.parse_args()

    horizons = tuple(int(x.strip()) for x in args.horizons.split(",") if x.strip())

    log_progress_start(_SCOPE, "开始", detail=f"{args.start} ~ {args.end}")
    try:
        dates = [
            to_iso(d)
            for d in trading_day_list(date.fromisoformat(args.start), date.fromisoformat(args.end))
        ]
        if not dates:
            log_progress_error(_SCOPE, "失败", detail="无交易日")
            sys.exit(1)

        daily = load_adjusted_daily()
        if not args.static:
            ts = fit_walk_forward_weights(
                dates,
                daily,
                train_window=args.train_window,
                step=args.step,
                min_icir=args.min_icir,
                min_tstat=args.min_tstat,
                fdr_alpha=args.fdr_alpha,
                horizon=args.horizon,
                horizons=horizons,
            )
            path = config_file("factor_weights_ts.yml")
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "apply": True,
                "meta": {
                    "train_window": args.train_window,
                    "step": args.step,
                    "horizon": args.horizon,
                    "horizons": list(horizons),
                    "fit_start": args.start,
                    "fit_end": args.end,
                },
                "weights_ts": ts,
            }
            with open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)
            print(f"walk-forward 权重表 → {path} | {len(ts)} 个日期")
            log_progress_done(_SCOPE, "成功", detail=f"{len(ts)} folds → {path}")
            return

        panel = build_panel(dates, daily=daily, universe_by_date=_universe_by_date(dates, daily))
        print(f"面板 {len(panel)} 行")
        names = REGISTRY.names()
        report_list = factor_ic_report(panel, names, horizon=args.horizon, horizons=horizons)
        report = {r["factor"]: r for r in report_list if r.get("factor")}
        full = full_weight_map(
            report, names, min_icir=args.min_icir, min_tstat=args.min_tstat, fdr_alpha=args.fdr_alpha or None
        )
        weights = {k: round(v, 4) for k, v in full.items()}
        n_pos = sum(1 for v in weights.values() if v > 0)

        out = {
            "apply": True,
            "fit_start": args.start,
            "fit_end": args.end,
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
        print(f"写入 {path} | 入选 {n_pos}/{len(names)} 因子（静态，勿用于 strict OOS live）")
        log_progress_done(_SCOPE, "成功", detail=f"static {n_pos}/{len(names)} → {path}")
    except SystemExit:
        raise
    except Exception as e:
        log_progress_error(_SCOPE, "失败", detail=f"{type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    main()
