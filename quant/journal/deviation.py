"""偏离日志：记录系统建议 vs 实际操作，量化人为偏离。

辅助决策的反身性工具：长期看偏离方向，判断情绪化交易来源。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class DeviationEntry:
    date: str
    code: str
    suggested_side: str  # buy/sell/hold/reduce/add
    suggested_weight: float
    actual_side: str  # 实际做了什么
    actual_weight: float
    deviation: float  # actual - suggested
    note: str = ""


class DeviationJournal:
    def __init__(self) -> None:
        self._entries: list[DeviationEntry] = []

    def record(self, entry: DeviationEntry) -> None:
        self._entries.append(entry)

    def record_from_card(self, card, actual: dict[str, tuple[str, float]]) -> None:
        """actual: {code: (actual_side, actual_weight)}。"""
        for a in card.actions:
            act = actual.get(a.code, (a.side, a.current_weight))
            self._entries.append(DeviationEntry(
                date=card.date, code=a.code,
                suggested_side=a.side, suggested_weight=a.target_weight,
                actual_side=act[0], actual_weight=act[1],
                deviation=act[1] - a.target_weight,
            ))

    def summary(self) -> dict:
        if not self._entries:
            return {"n": 0}
        n = len(self._entries)
        obeyed = sum(1 for e in self._entries if e.actual_side == e.suggested_side)
        # 情绪化偏离：建议 hold/sell 但实际 buy，或建议 buy 但实际 sell
        emotional = 0
        for e in self._entries:
            if e.suggested_side in ("hold", "sell") and e.actual_side == "buy":
                emotional += 1
            elif e.suggested_side == "buy" and e.actual_side == "sell":
                emotional += 1
        return {
            "n": n,
            "obey_rate": round(obeyed / n, 3),
            "emotional_deviation": emotional,
            "emotional_rate": round(emotional / n, 3),
            "avg_weight_deviation": round(sum(abs(e.deviation) for e in self._entries) / n, 4),
        }

    def entries(self) -> list[DeviationEntry]:
        return list(self._entries)
