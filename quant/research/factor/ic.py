"""因子 IC 工具：Spearman rank IC。

基于 ScoreSample 的旧 IC 报告（daily_cross_sectional_rank_ic / compute_dimension_ic /
factor_report）随评分体系退役；新的截面 IC 分析见 ``quant.factors.ic``（基于 FactorRow）。
本模块仅保留 ``spearman_ic`` 供 ``factors.report`` 复用。
"""

from __future__ import annotations

import numpy as np


def spearman_ic(scores: np.ndarray, returns: np.ndarray) -> float:
    if len(scores) < 3:
        return 0.0
    from scipy.stats import spearmanr

    corr, _ = spearmanr(scores, returns)
    return float(corr) if corr == corr else 0.0
