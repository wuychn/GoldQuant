"""Walk-forward 验证：滚动 train/test，杜绝全样本拟合。

将交易日序列切成连续窗口：每段 train_size 训练（调参/选因子），
test_size 样本外检验；拼接所有 test 段得样本外绩效。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import numpy as np


@dataclass
class WalkForwardResult:
    oos_returns: list[float]  # 样本外日收益序列
    oos_sharpe: float
    oos_ann_return: float
    oos_ann_vol: float
    n_test_days: int
    n_folds: int
    fold_sharpe: list[float]


def walk_forward(
    dates: list[str],
    *,
    train_size: int = 252,
    test_size: int = 63,
    step: int = 63,
    run_fn: Callable[[list[str], list[str]], dict[str, Any]] | None = None,
    equity_fn: Callable[[list[str]], list[float]] | None = None,
    trading_days: int = 252,
) -> WalkForwardResult:
    """run_fn(train_dates, test_dates) → {'equity': [...]}；或 equity_fn(test_dates) → 每日权益。

    简化：用 equity_fn 在每段 test 上算权益，拼成样本外收益。
    """
    n = len(dates)
    if n < train_size + test_size:
        return WalkForwardResult([], 0.0, 0.0, 0.0, 0, 0, [])

    oos_eq: list[float] = []
    fold_sharpe: list[float] = []
    start = 0
    folds = 0
    while start + train_size + test_size <= n:
        train = dates[start : start + train_size]
        test = dates[start + train_size : start + train_size + test_size]
        if run_fn is not None:
            res = run_fn(train, test)
            eq = res.get("equity", [])
        elif equity_fn is not None:
            eq = equity_fn(test)
        else:
            eq = []
        if len(eq) >= 2:
            vals = np.array(eq, dtype=float)
            rets = vals[1:] / vals[:-1] - 1.0
            if len(rets) > 1:
                sd = rets.std(ddof=1) * np.sqrt(trading_days)
                sh = (rets.mean() * trading_days) / sd if sd > 1e-12 else 0.0
                fold_sharpe.append(float(sh))
            oos_eq.extend(eq)
        start += step
        folds += 1

    if len(oos_eq) < 2:
        return WalkForwardResult([], 0.0, 0.0, 0.0, 0, folds, fold_sharpe)
    vals = np.array(oos_eq, dtype=float)
    rets = vals[1:] / vals[:-1] - 1.0
    ann_ret = float(rets.mean() * trading_days)
    ann_vol = float(rets.std(ddof=1) * np.sqrt(trading_days))
    sharpe = ann_ret / ann_vol if ann_vol > 1e-12 else 0.0
    return WalkForwardResult(
        oos_returns=rets.tolist(),
        oos_sharpe=round(sharpe, 3),
        oos_ann_return=round(ann_ret, 4),
        oos_ann_vol=round(ann_vol, 4),
        n_test_days=len(rets),
        n_folds=folds,
        fold_sharpe=fold_sharpe,
    )
