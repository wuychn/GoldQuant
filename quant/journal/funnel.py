"""漏斗统计：候选池 → 入选 → 买入 → 持有 → 盈利。

辅助决策的反身性工具：看每环节转化率，定位「选股多但不敢买 / 买了拿不住」。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FunnelRecord:
    date: str
    code: str
    in_universe: bool = False
    in_target: bool = False
    bought: bool = False
    held_days: int = 0
    pnl_pct: float | None = None


class FunnelTracker:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], FunnelRecord] = {}

    def observe_universe(self, date: str, codes: list[str]) -> None:
        for c in codes:
            key = (date, c)
            r = self._records.get(key)
            if r is None:
                r = FunnelRecord(date=date, code=c)
                self._records[key] = r
            r.in_universe = True

    def observe_target(self, date: str, codes: list[str]) -> None:
        for c in codes:
            key = (date, c)
            r = self._records.get(key)
            if r is None:
                r = FunnelRecord(date=date, code=c)
                self._records[key] = r
            r.in_target = True

    def observe_buy(self, date: str, code: str) -> None:
        key = (date, code)
        r = self._records.get(key)
        if r is None:
            r = FunnelRecord(date=date, code=code)
            self._records[key] = r
        r.bought = True

    def record_outcome(self, date: str, code: str, held_days: int, pnl_pct: float) -> None:
        key = (date, code)
        r = self._records.get(key)
        if r is None:
            r = FunnelRecord(date=date, code=code)
            self._records[key] = r
        r.held_days = held_days
        r.pnl_pct = pnl_pct

    def summary(self) -> dict:
        recs = list(self._records.values())
        if not recs:
            return {"n": 0}
        n_universe = sum(1 for r in recs if r.in_universe)
        n_target = sum(1 for r in recs if r.in_target)
        n_bought = sum(1 for r in recs if r.bought)
        closed = [r for r in recs if r.pnl_pct is not None]
        n_win = sum(1 for r in closed if r.pnl_pct > 0)
        return {
            "n_universe": n_universe,
            "n_target": n_target,
            "n_bought": n_bought,
            "conv_universe_to_target": round(n_target / max(n_universe, 1), 3),
            "conv_target_to_buy": round(n_bought / max(n_target, 1), 3),
            "n_closed": len(closed),
            "win_rate": round(n_win / max(len(closed), 1), 3),
            "avg_pnl_pct": round(sum(r.pnl_pct for r in closed) / max(len(closed), 1), 2) if closed else 0.0,
            "avg_held_days": round(sum(r.held_days for r in closed) / max(len(closed), 1), 1) if closed else 0.0,
        }
