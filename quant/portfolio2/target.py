"""目标组合：alpha 排名 → 逆波动率加权 → 缩放目标波动 → 约束 → 缓冲区。

实现 backtest2.PortfolioPolicy 协议，替代 EqualWeightTopN。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from quant.portfolio2.buffer import apply_buffer
from quant.portfolio2.constraints import apply_sector_cap, apply_single_cap, truncate_to_n
from quant.portfolio2.voltarget import inv_vol_weights, realized_vol, scale_to_target_vol


@dataclass
class TargetPortfolio:
    """主升波段目标组合策略。

    依赖 daily_df（离线库）算实现波动率；sectors 代码→行业映射可空。
    """

    n: int = 10
    target_vol: float = 0.15  # 组合年化目标波动
    max_weight: float = 0.15  # 单票上限
    sector_cap: float = 0.30  # 行业上限
    full_invest: float = 0.95  # 满仓比例（余现金）
    buffer_abs: float = 0.01
    buffer_rel: float = 0.20
    drop_tol: float = 0.015
    vol_lookback: int = 20
    daily: pd.DataFrame = field(default_factory=pd.DataFrame)
    sectors: dict[str, str] = field(default_factory=dict)

    def _vols(self, codes: list[str], as_of: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for c in codes:
            v = realized_vol(self.daily, c, as_of, lookback=self.vol_lookback)
            if v is None or v < 1e-6:
                v = 0.30  # 缺失用 30% 兜底
            out[c] = v
        return out

    def target_weights(self, alpha, prices, current, date):
        # 1. alpha 取前 N
        ranked = sorted(alpha.items(), key=lambda kv: -kv[1])[: self.n]
        codes = [c for c, _ in ranked if c in prices]
        if not codes:
            return {}
        # 2. 逆波动率加权
        vols = self._vols(codes, date)
        w = inv_vol_weights(vols, max_weight=self.max_weight)
        # 3. 缩放到目标波动
        w = scale_to_target_vol(w, vols, self.target_vol)
        # 4. 单票 + 行业约束
        w = apply_single_cap(w, self.max_weight)
        if self.sectors:
            w = apply_sector_cap(w, self.sectors, self.sector_cap)
        w = truncate_to_n(w, self.n)
        # 5. 缩放到满仓比例
        s = sum(w.values())
        if s > 0:
            k = self.full_invest / s
            w = {c: v * k for c, v in w.items()}
        # 6. 缓冲区
        w = apply_buffer(w, current, abs_tol=self.buffer_abs, rel_tol=self.buffer_rel, drop_tol=self.drop_tol)
        return w
