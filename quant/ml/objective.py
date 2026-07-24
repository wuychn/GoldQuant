"""ML 优化目标：按日等权组合收益的 OOS Sharpe / Calmar。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from quant.ml.dataset import ScoreSample


def daily_equal_weight_returns(samples: list[ScoreSample]) -> list[float]:
    """截面样本 → 按日等权组合收益序列（小数，非百分比）。"""
    by_date: dict[str, list[float]] = defaultdict(list)
    for s in samples:
        if s.forward_return_pct is None:
            continue
        by_date[s.date].append(float(s.forward_return_pct) / 100.0)
    if not by_date:
        return []
    return [sum(rets) / len(rets) for _, rets in sorted(by_date.items())]


def sharpe_from_daily_returns(daily_returns: list[float]) -> float:
    if len(daily_returns) < 5:
        return 0.0
    arr = np.array(daily_returns, dtype=float)
    std = arr.std(ddof=1)
    if std < 1e-9:
        return 0.0
    return float(arr.mean() / std * np.sqrt(252))


def portfolio_sharpe_from_samples(samples: list[ScoreSample]) -> float:
    """用按日等权组合收益计算年化 Sharpe（纠正把截面当时间序列的偏差）。"""
    return sharpe_from_daily_returns(daily_equal_weight_returns(samples))


def proxy_sharpe_from_samples(samples: list[ScoreSample]) -> float:
    """兼容旧名：等价于 portfolio_sharpe_from_samples。"""
    return portfolio_sharpe_from_samples(samples)


def calmar_from_daily_returns(daily_returns: list[float]) -> float:
    if len(daily_returns) < 5:
        return 0.0
    arr = np.array(daily_returns, dtype=float)
    equity = np.cumprod(1.0 + arr)
    peak = np.maximum.accumulate(equity)
    dd = (peak - equity) / np.maximum(peak, 1e-12)
    max_dd = float(dd.max()) if len(dd) else 0.0
    if max_dd < 1e-9:
        return float(arr.mean() * 252) if arr.mean() > 0 else 0.0
    ann = float(arr.mean() * 252)
    return ann / max_dd


def evaluate_objective(
    samples: list[ScoreSample],
    *,
    objective: str = "sharpe",
) -> dict[str, Any]:
    daily = daily_equal_weight_returns(samples)
    sharpe = sharpe_from_daily_returns(daily)
    with_fwd = [s for s in samples if s.forward_return_pct is not None]
    pos_rate = (
        sum(1 for s in with_fwd if s.label >= 0.5) / max(len(with_fwd), 1)
        if with_fwd
        else 0.0
    )
    out = {
        "proxy_sharpe": round(sharpe, 4),
        "portfolio_sharpe": round(sharpe, 4),
        "positive_rate": round(pos_rate, 4),
        "n_with_forward_return": len(with_fwd),
        "n_days": len(daily),
    }
    if objective == "calmar":
        out["proxy_calmar"] = round(calmar_from_daily_returns(daily), 4)
    return out


def score_threshold_objective(
    samples: list[ScoreSample],
    *,
    watchlist_threshold: float,
    objective: str = "sharpe",
) -> float:
    """对 total>=wt 子集评估目标；无合格样本返回极低分。"""
    selected = [s for s in samples if s.total >= watchlist_threshold]
    if len(selected) < 5:
        return -1e9
    if objective == "f1_legacy":
        y = np.array([s.label for s in samples], dtype=float)
        totals = np.array([s.total for s in samples], dtype=float)
        pred = ((totals >= watchlist_threshold) & (y >= 0.5)).astype(float)
        if pred.sum() == 0:
            return -1e9
        precision = float((pred * y).sum() / pred.sum())
        recall = float((pred * y).sum() / max(y.sum(), 1))
        return float(2 * precision * recall / max(precision + recall, 1e-9))
    daily = daily_equal_weight_returns(selected)
    if objective == "calmar":
        return calmar_from_daily_returns(daily)
    return sharpe_from_daily_returns(daily)
