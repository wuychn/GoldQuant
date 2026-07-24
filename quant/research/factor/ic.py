"""因子 IC / RankIC 分析。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from quant.ml.dataset import ScoreSample


def spearman_ic(scores: np.ndarray, returns: np.ndarray) -> float:
    if len(scores) < 3:
        return 0.0
    from scipy.stats import spearmanr

    corr, _ = spearmanr(scores, returns)
    return float(corr) if corr == corr else 0.0


def daily_cross_sectional_rank_ic(
    samples: list[ScoreSample],
    *,
    score_fn=None,
    min_names: int = 5,
) -> dict[str, Any]:
    """按交易日截面计算 RankIC（Spearman），再汇总 IC 均值 / ICIR。

    score_fn: sample → float；默认用总分。缺 forward_return 的样本跳过。
    """
    if score_fn is None:
        score_fn = lambda s: s.total  # noqa: E731

    by_date: dict[str, list[ScoreSample]] = defaultdict(list)
    for s in samples:
        if s.forward_return_pct is None:
            continue
        by_date[s.date].append(s)

    daily_ics: list[float] = []
    for _date, rows in sorted(by_date.items()):
        if len(rows) < min_names:
            continue
        scores = np.array([float(score_fn(r)) for r in rows], dtype=float)
        rets = np.array([float(r.forward_return_pct) for r in rows], dtype=float)
        daily_ics.append(spearman_ic(scores, rets))

    if not daily_ics:
        return {
            "ic_mean": 0.0,
            "ic_std": 0.0,
            "icir": 0.0,
            "n_days": 0,
            "method": "daily_rank_ic",
        }
    arr = np.array(daily_ics, dtype=float)
    ic_mean = float(arr.mean())
    ic_std = float(arr.std(ddof=1)) if len(arr) > 1 else 0.0
    icir = ic_mean / ic_std if ic_std > 1e-12 else 0.0
    return {
        "ic_mean": round(ic_mean, 4),
        "ic_std": round(ic_std, 4),
        "icir": round(icir, 4),
        "n_days": len(daily_ics),
        "method": "daily_rank_ic",
    }


def compute_dimension_ic(
    samples: list[ScoreSample],
    *,
    dim_key: str,
) -> dict[str, Any]:
    """单维度：日度截面 RankIC（主）+ 全样本池化 Spearman（辅）。"""
    filtered = [
        s for s in samples if dim_key in s.dim_scores and s.forward_return_pct is not None
    ]
    daily = daily_cross_sectional_rank_ic(
        filtered,
        score_fn=lambda s, k=dim_key: float(s.dim_scores[k]),
    )

    scores = [s.dim_scores[dim_key] for s in filtered]
    rets = [float(s.forward_return_pct) for s in filtered]
    pooled = spearman_ic(np.array(scores), np.array(rets)) if len(scores) >= 10 else 0.0
    return {
        "dim": dim_key,
        "ic_mean": daily["ic_mean"],
        "ic_std": daily["ic_std"],
        "icir": daily["icir"],
        "n_days": daily["n_days"],
        "pooled_ic": round(pooled, 4),
        "count": len(filtered),
        "method": "daily_rank_ic",
    }


def factor_report(samples: list[ScoreSample]) -> list[dict[str, Any]]:
    """全维度 IC 报告 + 总分日度 RankIC。"""
    if not samples:
        return []
    dims: set[str] = set()
    for s in samples:
        dims.update(s.dim_scores.keys())
    rows = [compute_dimension_ic(samples, dim_key=d) for d in sorted(dims)]
    total_ic = daily_cross_sectional_rank_ic(samples)
    rows.insert(
        0,
        {
            "dim": "__total__",
            "ic_mean": total_ic["ic_mean"],
            "ic_std": total_ic["ic_std"],
            "icir": total_ic["icir"],
            "n_days": total_ic["n_days"],
            "pooled_ic": None,
            "count": sum(1 for s in samples if s.forward_return_pct is not None),
            "method": "daily_rank_ic",
        },
    )
    return rows
