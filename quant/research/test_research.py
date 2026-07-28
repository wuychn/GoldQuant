"""P5 参数治理与验证单元测试。"""

from __future__ import annotations

import numpy as np

from quant.research.sensitivity import scan_param, parameter_budget
from quant.research.significance import deflated_sharpe
from quant.research.walk_forward import walk_forward


def test_walk_forward_runs():
    dates = [f"2024-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 29)]  # ~336 唯一日
    dates = sorted(set(dates))
    assert len(dates) >= 200 + 60
    # 假权益：test 段每段线性增长
    def equity_fn(test_dates):
        n = len(test_dates)
        return [1_000_000 * (1 + 0.001 * i) for i in range(n)]
    res = walk_forward(dates, train_size=200, test_size=60, step=60, equity_fn=equity_fn)
    assert res.n_folds > 0
    assert res.oos_sharpe > 0


def test_dsr_decreases_with_trials():
    # 同一 Sharpe，试验越多越不显著
    s1 = deflated_sharpe(1.5, n=252, n_trials=1)
    s100 = deflated_sharpe(1.5, n=252, n_trials=100)
    assert s100["dsr"] <= s1["dsr"]


def test_dsr_high_sharpe_significant():
    res = deflated_sharpe(3.0, n=500, n_trials=5)
    assert res["dsr"] > 0.8


def test_sensitivity_stability():
    # 平稳参数：Sharpe 在网格上波动小
    res = scan_param("atr_mult", [2.0, 2.5, 3.0, 3.5, 4.0], run_fn=lambda v: 1.2 + 0.01 * (v - 3.0))
    assert res.stability > 0.9
    # 尖峰参数：单点极高
    res2 = scan_param("hard_pct", [0.05, 0.08, 0.10], run_fn=lambda v: 3.0 if abs(v - 0.08) < 1e-6 else 0.5)
    assert res2.peak_to_median > 1.5


def test_parameter_budget():
    assert parameter_budget(3, 5) == 125
