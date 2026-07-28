"""资金族：主力净流入/流通市值。

优先级：``bars.extras['flow_ratio_5']`` → PIT 快照（panel_builder 注入）
→ 量价代理（近5日 vs 前5日成交额差 / 流通市值，可回测）。
"""

from __future__ import annotations

import pandas as pd

from quant.factors.library.base import BarSeries, FactorDef


def _flow_proxy(bars: BarSeries, as_of: str) -> float | None:
    df = bars.df.loc[bars.df.index <= as_of]
    if "amount" not in df.columns or len(df) < 11:
        return None
    amt = pd.to_numeric(df["amount"], errors="coerce").dropna()
    if len(amt) < 11:
        return None
    recent = float(amt.iloc[-5:].sum())
    prev = float(amt.iloc[-10:-5].sum())
    float_mv = None
    if "float_mv" in df.columns:
        mv = pd.to_numeric(df["float_mv"], errors="coerce").dropna()
        if not mv.empty:
            float_mv = float(mv.iloc[-1])
    if not float_mv or float_mv <= 0:
        return None
    return (recent - prev) / float_mv


def flow_ratio_5(bars: BarSeries, as_of: str) -> float | None:
    extras = getattr(bars, "extras", None) or {}
    v = extras.get("flow_ratio_5")
    if v is not None:
        try:
            f = float(v)
            return f if abs(f) < 1e8 else None
        except (TypeError, ValueError):
            pass
    return _flow_proxy(bars, as_of)


FLOW_FACTORS: list[FactorDef] = [
    FactorDef("flow_ratio_5", "5日主力净流入/市值", flow_ratio_5, direction=1.0, default_weight=0.6),
]
