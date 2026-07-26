"""已退役：旧每周回测/ML 推送。

请改用::
    python -m scripts.backtest.run ...
    python -m scripts.research.walk_forward ...
"""

from __future__ import annotations


def run_weekly_backtest(settings=None) -> None:
    raise SystemExit("已退役：请使用 python -m scripts.backtest.run / scripts.research.walk_forward")


def run_weekly_ml(settings=None) -> None:
    raise SystemExit("已退役：r3 不再做维度权重 ML 校准")
