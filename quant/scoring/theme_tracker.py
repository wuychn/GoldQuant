"""主线题材跟踪：多日 persistence，过滤单日脉冲/轮动噪音。

当日涨幅榜或资金榜前列 ≠ 主线。主线需在近 N 日内反复出现，且至少有一次资金榜确认。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from quant.config import load_gates_config
from quant.store.paths import state_file

_SH_TZ = ZoneInfo("Asia/Shanghai")
_STATE_NAME = "main_themes.json"


def _theme_cfg() -> dict[str, Any]:
    gates = load_gates_config()
    return gates.get("main_theme") or {}


def _board_rows(payload: dict, key: str, limit: int) -> list[dict]:
    boards = payload.get("概念板块") or {}
    rows = boards.get(key) or []
    return [r for r in rows[:limit] if isinstance(r, dict)]


def snapshot_boards(payload: dict, *, limit: int = 10) -> tuple[set[str], set[str]]:
    """当日涨幅榜 / 资金流入榜概念名集合。"""
    gain: set[str] = set()
    fund: set[str] = set()
    for row in _board_rows(payload, "涨幅榜", limit):
        n = str(row.get("行业", "")).strip()
        if n:
            gain.add(n)
    for row in _board_rows(payload, "资金流入榜", limit):
        n = str(row.get("行业", "")).strip()
        if n:
            fund.add(n)
    return gain, fund


def _load_state() -> dict[str, Any]:
    path = state_file(_STATE_NAME)
    if not path.is_file():
        return {"concepts": {}, "last_update_date": ""}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"concepts": {}, "last_update_date": ""}
    if not isinstance(raw, dict):
        return {"concepts": {}, "last_update_date": ""}
    raw.setdefault("concepts", {})
    return raw


def _save_state(state: dict[str, Any]) -> None:
    path = state_file(_STATE_NAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _today() -> str:
    return datetime.now(_SH_TZ).strftime("%Y-%m-%d")


def _window_start(lookback: int) -> str:
    return (date.today() - timedelta(days=lookback - 1)).isoformat()


def _history_in_window(history: dict[str, Any], lookback: int) -> dict[str, dict[str, bool]]:
    cutoff = _window_start(lookback)
    out: dict[str, dict[str, bool]] = {}
    for d, flags in (history or {}).items():
        if d >= cutoff and isinstance(flags, dict):
            out[d] = {
                "gain": bool(flags.get("gain")),
                "fund": bool(flags.get("fund")),
            }
    return out


def update_main_theme_state(payload: dict) -> dict[str, Any]:
    """按日写入概念上榜记录（同一自然日只记一次）。"""
    cfg = _theme_cfg()
    limit = int(cfg.get("board_limit", 10))
    lookback = int(cfg.get("lookback_days", 5))
    today = _today()
    gain, fund = snapshot_boards(payload, limit=limit)
    state = _load_state()
    concepts: dict[str, dict] = state.setdefault("concepts", {})

    for name in gain | fund:
        entry = concepts.setdefault(name, {"history": {}})
        history: dict[str, Any] = entry.setdefault("history", {})
        if today in history:
            continue
        history[today] = {"gain": name in gain, "fund": name in fund}
        entry["history"] = _history_in_window(history, lookback)

    state["last_update_date"] = today
    state["today_gain"] = sorted(gain)
    state["today_fund"] = sorted(fund)
    state["today_dual"] = sorted(gain & fund)
    _save_state(state)
    return state


def resolve_main_themes(payload: dict, *, update: bool = True) -> set[str]:
    """确认主线：窗口内上榜天数 ≥ min_hit_days，且窗口内至少 1 次资金榜。"""
    cfg = _theme_cfg()
    state = update_main_theme_state(payload) if update else _load_state()
    lookback = int(cfg.get("lookback_days", 5))
    min_days = int(cfg.get("min_hit_days", 2))
    require_fund = bool(cfg.get("require_fund_once", True))

    confirmed: set[str] = set()
    for name, entry in (state.get("concepts") or {}).items():
        hist = _history_in_window(entry.get("history") or {}, lookback)
        if len(hist) < min_days:
            continue
        fund_hits = sum(1 for f in hist.values() if f.get("fund"))
        if require_fund and fund_hits < 1:
            continue
        confirmed.add(name)

    return confirmed


def theme_detail(payload: dict) -> dict[str, Any]:
    """供评分维度输出的调试信息。"""
    gain, fund = snapshot_boards(payload, limit=int(_theme_cfg().get("board_limit", 10)))
    main = resolve_main_themes(payload)
    return {
        "当日涨幅概念": sorted(gain),
        "当日资金概念": sorted(fund),
        "确认主线": sorted(main),
    }
