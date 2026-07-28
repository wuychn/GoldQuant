"""组合策略接口 + 简单实现。

P3 会提供完整实现（波动率目标 + 缓冲区 + 约束）。
P2 用 EqualWeightTopN 让回测可跑。
"""

from __future__ import annotations

from typing import Protocol


class PortfolioPolicy(Protocol):
    def target_weights(
        self,
        alpha: dict[str, float],
        prices: dict[str, float],
        current: dict[str, float],  # {code: weight}
        date: str,
    ) -> dict[str, float]:
        """返回目标权重 {code: weight}，权重和应 <= 1（余为现金）。"""
        ...


class EqualWeightTopN:
    """取 alpha 前 N，等权（接近满仓），其余现金。"""

    def __init__(self, n: int = 10, full_invest: float = 0.95):
        self.n = n
        self.full_invest = full_invest

    def target_weights(self, alpha, prices, current, date):
        ranked = sorted(alpha.items(), key=lambda kv: -kv[1])
        top = [(c, v) for c, v in ranked[: self.n] if c in prices]
        if not top:
            return {}
        w = self.full_invest / len(top)
        return {code: w for code, _ in top}




