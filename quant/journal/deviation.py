"""偏离日志：记录系统建议 vs 实际操作，量化人为偏离。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class DeviationEntry:
    date: str
    code: str
    suggested_side: str  # buy/sell/hold/reduce/add
    suggested_weight: float
    actual_side: str
    actual_weight: float
    deviation: float  # actual - suggested
    note: str = ""
    reason: str = ""  # 未执行原因枚举


class DeviationJournal:
    def __init__(self, path: str | Path | None = None) -> None:
        self._entries: list[DeviationEntry] = []
        self.path = Path(path) if path else None
        if self.path and self.path.is_file():
            self.load()

    def record(self, entry: DeviationEntry) -> None:
        self._entries.append(entry)

    def record_from_card(self, card, actual: dict[str, tuple[str, float]]) -> None:
        """actual: {code: (actual_side, actual_weight)}。"""
        for a in card.actions:
            act = actual.get(a.code, (a.side, a.current_weight))
            self._entries.append(
                DeviationEntry(
                    date=card.date,
                    code=a.code,
                    suggested_side=a.side,
                    suggested_weight=a.target_weight,
                    actual_side=act[0],
                    actual_weight=act[1],
                    deviation=act[1] - a.target_weight,
                )
            )

    def summary(self) -> dict:
        if not self._entries:
            return {"n": 0}
        n = len(self._entries)
        obeyed = sum(1 for e in self._entries if e.actual_side == e.suggested_side)
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

    def monthly_attribution(self) -> dict[str, dict]:
        """按 YYYY-MM 分组的偏离归因。"""
        by_m: dict[str, list[DeviationEntry]] = {}
        for e in self._entries:
            key = str(e.date)[:7]
            by_m.setdefault(key, []).append(e)
        out: dict[str, dict] = {}
        for m, ents in sorted(by_m.items()):
            n = len(ents)
            obeyed = sum(1 for e in ents if e.actual_side == e.suggested_side)
            by_reason: dict[str, int] = {}
            for e in ents:
                if e.actual_side != e.suggested_side:
                    r = e.reason or e.note or "unspecified"
                    by_reason[r] = by_reason.get(r, 0) + 1
            out[m] = {
                "n": n,
                "obey_rate": round(obeyed / n, 3) if n else 0.0,
                "avg_abs_deviation": round(sum(abs(e.deviation) for e in ents) / n, 4) if n else 0.0,
                "by_reason": by_reason,
            }
        return out

    def entries(self) -> list[DeviationEntry]:
        return list(self._entries)

    def save(self, path: str | Path | None = None) -> None:
        p = Path(path) if path else self.path
        if p is None:
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            for e in self._entries:
                f.write(json.dumps(asdict(e), ensure_ascii=False) + "\n")

    def load(self, path: str | Path | None = None) -> None:
        p = Path(path) if path else self.path
        if p is None or not p.is_file():
            return
        self._entries = []
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                self._entries.append(DeviationEntry(**{k: d[k] for k in DeviationEntry.__dataclass_fields__ if k in d}))
