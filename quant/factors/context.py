"""因子链运行上下文（从 quant.scoring.context 迁出）。

r1 评分退役后，``ScoreContext`` 仍被 portfolio / store / factors 用作单次运行的
payload 容器。轻量 dataclass，无 scoring 语义。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ScoreContext:
    """单次运行的 payload 上下文。"""

    payload: dict
    mode: str = ""

    @classmethod
    def from_payload(cls, payload: dict, *, mode: str = "") -> ScoreContext:
        return cls(payload=payload, mode=mode)
