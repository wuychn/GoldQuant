"""ML 标签：选股用前瞻收益；策略 PnL 另计。"""

from __future__ import annotations

from dataclasses import dataclass

from quant.ml.dataset import ScoreSample


@dataclass
class TradeLabel:
    date: str
    code: str
    label: float
    forward_return_pct: float | None = None
    realized_pnl: float | None = None


def selection_label_from_sample(sample: ScoreSample) -> float:
    """选股标签：仅基于前瞻收益方向（与 sample.label 一致）。"""
    if sample.forward_return_pct is not None:
        return 1.0 if sample.forward_return_pct > 0 else 0.0
    return 1.0 if sample.label >= 0.5 else 0.0


def build_selection_labels(samples: list[ScoreSample]) -> list[TradeLabel]:
    return [
        TradeLabel(
            date=s.date,
            code=s.code,
            label=selection_label_from_sample(s),
            forward_return_pct=s.forward_return_pct,
        )
        for s in samples
        if s.forward_return_pct is not None
    ]
