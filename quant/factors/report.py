"""因子 RankIC：原始 vs 中性化对比报告。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from quant.factors.base import FactorRow
from quant.factors.compose import compose_row_alpha
from quant.factors.panel import FactorPanel, load_factor_panels_from_daily
from quant.factors.raw import factor_names
from quant.research.factor.ic import spearman_ic


def _ic_series_for_values(
    rows: list[FactorRow],
    *,
    value_fn,
    min_names: int = 5,
) -> dict[str, Any]:
    """按日截面 RankIC，再汇总。"""
    by_date: dict[str, list[FactorRow]] = defaultdict(list)
    for r in rows:
        if r.forward_return_pct is None:
            continue
        by_date[r.date].append(r)

    daily: list[float] = []
    for _d, group in sorted(by_date.items()):
        pairs = []
        for r in group:
            v = value_fn(r)
            if v is None:
                continue
            pairs.append((float(v), float(r.forward_return_pct)))
        if len(pairs) < min_names:
            continue
        scores = np.array([p[0] for p in pairs], dtype=float)
        rets = np.array([p[1] for p in pairs], dtype=float)
        daily.append(spearman_ic(scores, rets))

    if not daily:
        return {"ic_mean": 0.0, "ic_std": 0.0, "icir": 0.0, "n_days": 0}
    arr = np.array(daily, dtype=float)
    ic_mean = float(arr.mean())
    ic_std = float(arr.std(ddof=1)) if len(arr) > 1 else 0.0
    icir = ic_mean / ic_std if ic_std > 1e-12 else 0.0
    return {
        "ic_mean": round(ic_mean, 4),
        "ic_std": round(ic_std, 4),
        "icir": round(icir, 4),
        "n_days": len(daily),
    }


def compare_raw_vs_neutral_ic(
    panel: FactorPanel,
    *,
    horizon_label: str = "H",
    min_names: int = 5,
) -> dict[str, Any]:
    """逐因子 + 合成 alpha：原始 vs 中性化 RankIC。"""
    rows = panel.rows
    names = factor_names()
    per_factor: dict[str, Any] = {}
    for fname in names:
        raw_ic = _ic_series_for_values(
            rows,
            value_fn=lambda r, f=fname: r.raw.get(f),
            min_names=min_names,
        )
        neut_ic = _ic_series_for_values(
            rows,
            value_fn=lambda r, f=fname: r.neutral.get(f),
            min_names=min_names,
        )
        per_factor[fname] = {
            "raw": raw_ic,
            "neutral": neut_ic,
            "delta_ic_mean": round(neut_ic["ic_mean"] - raw_ic["ic_mean"], 4),
            "delta_icir": round(neut_ic["icir"] - raw_ic["icir"], 4),
        }

    raw_alpha = _ic_series_for_values(
        rows,
        value_fn=lambda r: compose_row_alpha(r, use_neutral=False),
        min_names=min_names,
    )
    neut_alpha = _ic_series_for_values(
        rows,
        value_fn=lambda r: compose_row_alpha(r, use_neutral=True),
        min_names=min_names,
    )

    # 行业暴露粗检：合成 alpha 与行业均值的相关（越低越好）
    exposure = _industry_alpha_exposure(rows)

    return {
        "horizon": horizon_label,
        "n_rows": len(rows),
        "n_with_forward": sum(1 for r in rows if r.forward_return_pct is not None),
        "factors": per_factor,
        "composite_alpha": {
            "raw": raw_alpha,
            "neutral": neut_alpha,
            "delta_ic_mean": round(neut_alpha["ic_mean"] - raw_alpha["ic_mean"], 4),
            "delta_icir": round(neut_alpha["icir"] - raw_alpha["icir"], 4),
        },
        "industry_exposure": exposure,
    }


def _industry_alpha_exposure(rows: list[FactorRow]) -> dict[str, Any]:
    """中性化后 alpha 在行业间的方差占比（越低说明中性化越干净）。"""
    by_date: dict[str, list[FactorRow]] = defaultdict(list)
    for r in rows:
        by_date[r.date].append(r)
    ratios: list[float] = []
    for group in by_date.values():
        alphas = []
        inds = []
        for r in group:
            a = compose_row_alpha(r, use_neutral=True)
            if a is None or not r.industry:
                continue
            alphas.append(a)
            inds.append(r.industry)
        if len(alphas) < 8 or len(set(inds)) < 2:
            continue
        arr = np.array(alphas, dtype=float)
        total_var = float(arr.var())
        if total_var < 1e-12:
            continue
        # 行业间方差
        ind_means = {}
        ind_counts = defaultdict(int)
        for a, ind in zip(alphas, inds):
            ind_means[ind] = ind_means.get(ind, 0.0) + a
            ind_counts[ind] += 1
        for ind in ind_means:
            ind_means[ind] /= ind_counts[ind]
        between = float(np.mean([(ind_means[i] - arr.mean()) ** 2 for i in inds]))
        ratios.append(between / total_var)
    if not ratios:
        return {"between_industry_var_ratio": None, "n_days": 0}
    return {
        "between_industry_var_ratio": round(float(np.mean(ratios)), 4),
        "n_days": len(ratios),
        "note": "越低越好；中性化后行业方差占比应下降",
    }


def run_factor_research(
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    horizon: int = 5,
    min_names: int = 5,
) -> dict[str, Any]:
    """端到端：加载 daily → 中性化 → 原始/中性 IC 对比。"""
    panel = load_factor_panels_from_daily(
        from_date=from_date,
        to_date=to_date,
        horizon=horizon,
        neutralize=True,
        min_names=min_names,
    )
    if not panel.rows:
        return {
            "reason": "无因子样本（检查 ~/.quant/daily 晚间快照与候选）",
            "n_rows": 0,
        }
    report = compare_raw_vs_neutral_ic(
        panel,
        horizon_label=f"{horizon}d",
        min_names=min_names,
    )
    # 额外：1日 horizon 对照（若数据够）
    if horizon != 1:
        panel_1 = load_factor_panels_from_daily(
            from_date=from_date,
            to_date=to_date,
            horizon=1,
            neutralize=True,
            min_names=min_names,
        )
        if panel_1.rows:
            report["horizon_1d"] = compare_raw_vs_neutral_ic(
                panel_1, horizon_label="1d", min_names=min_names
            )
    return report
