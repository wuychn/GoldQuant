"""资金族：主力净流入/流通市值。

实盘可用单日资金流；回测因资金流接口无历史快照，置空。
"""

from __future__ import annotations

from quant.factors.library.base import BarSeries, FactorDef


def flow_ratio_5(bars: BarSeries, as_of: str) -> float | None:
    """5 日主力净流入 / 流通市值。

    资金流数据不在 daily_raw 中（spot_em 不含主力净流入字段，且无历史），
    本因子在离线回测中返回 None；实盘链路可由 payload 注入后覆盖。
    """
    return None


FLOW_FACTORS: list[FactorDef] = [
    FactorDef("flow_ratio_5", "5日主力净流入/市值", flow_ratio_5, direction=1.0, default_weight=0.6),
]
