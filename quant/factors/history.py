"""板块榜历史：用于 persistence / 退潮判定。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from quant.store.paths import quant_home


def _path() -> Path:
    p = quant_home() / "state" / "sector_history.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_sector_history(*, max_days: int = 15) -> list[dict[str, Any]]:
    path = _path()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    snaps = raw.get("snapshots") if isinstance(raw, dict) else raw
    if not isinstance(snaps, list):
        return []
    return [s for s in snaps if isinstance(s, dict)][-max_days:]


def append_sector_snapshot(
    date_str: str,
    sectors: list[dict[str, Any]],
    *,
    max_days: int = 15,
    persist: bool = True,
    memory_history: list[dict] | None = None,
) -> None:
    if not date_str:
        return
    if memory_history is not None:
        memory_history[:] = [s for s in memory_history if s.get("date") != date_str]
        memory_history.append({"date": date_str, "sectors": sectors})
    if not persist:
        return
    snaps = [s for s in load_sector_history(max_days=max_days + 5) if s.get("date") != date_str]
    snaps.append({"date": date_str, "sectors": sectors})
    snaps = snaps[-max_days:]
    _path().write_text(
        json.dumps({"snapshots": snaps}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def days_on_gain_board(name: str, history: list[dict[str, Any]], *, on_board_today: bool) -> int:
    """连续出现在涨幅榜快照中的交易日数（含当日）。"""
    if not on_board_today:
        return 0
    days = 1
    for snap in reversed(history):
        if snap.get("date") is None:
            continue
        names = {
            str(s.get("name") or "").strip()
            for s in (snap.get("sectors") or [])
            if isinstance(s, dict)
        }
        if name in names:
            days += 1
        else:
            break
    return days


def days_since_on_board(name: str, history: list[dict[str, Any]]) -> int | None:
    """距上次在榜经过的交易日数；None 表示从未在榜。"""
    for i, snap in enumerate(reversed(history)):
        names = {
            str(s.get("name") or "").strip()
            for s in (snap.get("sectors") or [])
            if isinstance(s, dict)
        }
        if name in names:
            return i
    return None
