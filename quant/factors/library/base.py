"""因子库：按族组织，每个因子从单只 OHLCV 序列计算。

设计：因子函数接收 ``BarSeries``（date 索引的 OHLCV）与评估日 as_of，
返回 float | None。与旧 dict-based 因子解耦，直接吃离线库后复权序列。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BarSeries:
    """单只后复权 OHLCV，date 升序。"""

    code: str
    df: pd.DataFrame  # index=date(str), columns: open/high/low/close/volume/amount/turnover

    def close_up_to(self, as_of: str) -> pd.Series:
        return self.df.loc[self.df.index <= as_of, "close"]

    def high_up_to(self, as_of: str) -> pd.Series:
        return self.df.loc[self.df.index <= as_of, "high"]

    def volume_up_to(self, as_of: str) -> pd.Series:
        return self.df.loc[self.df.index <= as_of, "volume"]

    def amount_up_to(self, as_of: str) -> pd.Series:
        return self.df.loc[self.df.index <= as_of, "amount"]

    def turnover_up_to(self, as_of: str) -> pd.Series:
        return self.df.loc[self.df.index <= as_of, "turnover_rate"]


FactorFn = Callable[[BarSeries, str], float | None]


@dataclass(frozen=True)
class FactorDef:
    name: str
    description: str
    compute: FactorFn
    direction: float = 1.0  # +1 越大越看多；-1 越小越看多
    default_weight: float = 1.0


def _ret(series: pd.Series, n: int) -> float | None:
    if len(series) < n + 1:
        return None
    a = float(series.iloc[-n - 1])
    b = float(series.iloc[-1])
    if a <= 0:
        return None
    return b / a - 1.0


def _std_ret(series: pd.Series, n: int) -> float | None:
    if len(series) < n + 1:
        return None
    rets = series.pct_change().iloc[-n:].dropna()
    if len(rets) < 2:
        return None
    return float(rets.std(ddof=1))
