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


def concept_resonance_weights(payload: dict, *, update: bool = True) -> dict[str, float]:
    """各概念共振权重（0~100）：确认主线 + 涨幅榜排名 + 资金榜排名/净额。"""
    cfg = _theme_cfg()
    limit = int(cfg.get("board_limit", 10))
    w_cfg = cfg.get("score_weights") or {}
    w_main = float(w_cfg.get("confirmed", 40))
    w_gain = float(w_cfg.get("gain_rank", 25))
    w_fund_rank = float(w_cfg.get("fund_rank", 20))
    w_fund_amt = float(w_cfg.get("fund_amount", 15))

    main = resolve_main_themes(payload, update=update)
    gain_rank, fund_rank, fund_net = _board_rank_and_fund(payload, limit)
    max_net = max((v for v in fund_net.values() if v > 0), default=0.0)

    weights: dict[str, float] = {}
    for name in main | set(gain_rank) | set(fund_rank):
        w = 0.0
        if name in main:
            w += w_main
        if name in gain_rank:
            w += _rank_bonus(gain_rank[name], limit, w_gain)
        if name in fund_rank:
            w += _rank_bonus(fund_rank[name], limit, w_fund_rank)
            if max_net > 0:
                net = max(0.0, fund_net.get(name, 0.0))
                w += w_fund_amt * (net / max_net)
        weights[name] = round(w, 2)
    return weights


def _board_rank_and_fund(
    payload: dict,
    limit: int,
) -> tuple[dict[str, int], dict[str, int], dict[str, float]]:
    gain_rank: dict[str, int] = {}
    fund_rank: dict[str, int] = {}
    fund_net: dict[str, float] = {}
    for i, row in enumerate(_board_rows(payload, "涨幅榜", limit)):
        n = str(row.get("行业", "")).strip()
        if n and n not in gain_rank:
            gain_rank[n] = i + 1
    for i, row in enumerate(_board_rows(payload, "资金流入榜", limit)):
        n = str(row.get("行业", "")).strip()
        if n and n not in fund_rank:
            fund_rank[n] = i + 1
            try:
                fund_net[n] = float(row.get("净额") or 0)
            except (TypeError, ValueError):
                fund_net[n] = 0.0
    return gain_rank, fund_rank, fund_net


def _rank_bonus(rank: int, limit: int, max_pts: float) -> float:
    if rank <= 0 or rank > limit:
        return 0.0
    return max_pts * (limit - rank + 1) / limit


def score_concept_resonance(
    stock_concepts: set[str],
    payload: dict,
    *,
    update: bool = True,
) -> tuple[float, dict[str, Any]]:
    """个股概念与确认主线交集，按各概念权重计分。"""
    main = resolve_main_themes(payload, update=update)
    if not main:
        return 50.0, {"available": False}

    weights = concept_resonance_weights(payload, update=False)
    hits = stock_concepts & main
    if not hits:
        return 25.0, {"命中概念": [], "命中权重": {}}

    hit_weights = {c: weights.get(c, float((_theme_cfg().get("score_weights") or {}).get("confirmed", 40))) for c in hits}
    peak = max(hit_weights.values())
    extra = min(15.0, max(0, len(hits) - 1) * 5.0)
    score = min(100.0, 30.0 + peak * 0.7 + extra)
    return score, {"命中概念": sorted(hits), "命中权重": hit_weights, "峰值权重": round(peak, 2)}


def theme_detail(payload: dict) -> dict[str, Any]:
    """供评分维度输出的调试信息。"""
    gain, fund = snapshot_boards(payload, limit=int(_theme_cfg().get("board_limit", 10)))
    main = resolve_main_themes(payload)
    return {
        "当日涨幅概念": sorted(gain),
        "当日资金概念": sorted(fund),
        "确认主线": sorted(main),
        "概念权重": concept_resonance_weights(payload, update=False),
    }
