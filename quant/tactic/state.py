"""策略状态：观察池 / 次日计划 / 纸面持仓。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from quant.store.paths import quant_home

INITIAL_CAPITAL = 100_000.0
MAX_WEIGHT = 0.20
LIGHT_WEIGHT = 0.10  # 2进3 黄灯轻仓
MAX_PLAN = 3


def state_path() -> Path:
    return quant_home() / "store" / "tactic" / "state.json"


def load_state() -> dict[str, Any]:
    p = state_path()
    if not p.is_file():
        return {
            "watch": [],
            "plan": [],
            "positions": [],
            "pending_auction_exits": [],
            "cash": INITIAL_CAPITAL,
            "last_hash": "",
            "as_of": "",
        }
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {
            "watch": [],
            "plan": [],
            "positions": [],
            "pending_auction_exits": [],
            "cash": INITIAL_CAPITAL,
            "last_hash": "",
            "as_of": "",
        }


def save_state(st: dict[str, Any]) -> Path:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    return p
