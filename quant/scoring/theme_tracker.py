"""主线题材：滑动窗口内累计涨幅最大 + 累计资金流入最多，各 1 条（最多 2 条）。

日快照与主线确认仅在 ``post_market_evening`` 写入 ``main_themes.json``；
盘中/午间/盘前等模式只读已确认主线，不更新状态。
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
MAIN_THEME_UPDATE_MODE = "post_market_evening"


def should_update_main_theme(mode: str = "") -> bool:
    """是否允许写入 ``main_themes.json``（仅晚间复盘）。"""
    return mode == MAIN_THEME_UPDATE_MODE


def _theme_cfg() -> dict[str, Any]:
    gates = load_gates_config()
    return gates.get("main_theme") or {}


def _board_rows(payload: dict, key: str, limit: int) -> list[dict]:
    boards = payload.get("概念板块") or {}
    rows = boards.get(key) or []
    return [r for r in rows[:limit] if isinstance(r, dict)]


def _f(v: object, default: float = 0.0) -> float:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


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


def _snapshot_gain_rows(payload: dict, limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in _board_rows(payload, "涨幅榜", limit):
        name = str(row.get("行业", "")).strip()
        if not name:
            continue
        out.append({"行业": name, "行业-涨跌幅": _f(row.get("行业-涨跌幅"))})
    return out


def _snapshot_fund_rows(payload: dict, limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in _board_rows(payload, "资金流入榜", limit):
        name = str(row.get("行业", "")).strip()
        if not name:
            continue
        out.append({"行业": name, "净额": _f(row.get("净额"))})
    return out


def _load_state() -> dict[str, Any]:
    path = state_file(_STATE_NAME)
    if not path.is_file():
        return {"daily": {}, "last_update_date": ""}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"daily": {}, "last_update_date": ""}
    if not isinstance(raw, dict):
        return {"daily": {}, "last_update_date": ""}
    if "daily" not in raw:
        raw = {"daily": {}, "last_update_date": str(raw.get("last_update_date") or "")}
    raw.setdefault("daily", {})
    return raw


def _save_state(state: dict[str, Any]) -> None:
    path = state_file(_STATE_NAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _today() -> str:
    return datetime.now(_SH_TZ).strftime("%Y-%m-%d")


def _window_start(lookback: int) -> str:
    return (date.today() - timedelta(days=lookback - 1)).isoformat()


def _trim_daily(state: dict[str, Any], lookback: int) -> None:
    daily: dict[str, Any] = state.setdefault("daily", {})
    cutoff = _window_start(lookback)
    for key in list(daily.keys()):
        if key < cutoff:
            del daily[key]


def _max_by_total(totals: dict[str, float]) -> str | None:
    if not totals:
        return None
    return max(totals.items(), key=lambda x: (x[1], x[0]))[0]


def resolve_main_theme_leaders(state: dict[str, Any], *, lookback: int | None = None) -> tuple[str | None, str | None]:
    """滑动窗口内：累计涨幅最大概念、累计资金净流入最大概念（各 1 条）。"""
    cfg = _theme_cfg()
    window = lookback if lookback is not None else int(cfg.get("lookback_days", 5))
    cutoff = _window_start(window)
    gain_totals: dict[str, float] = {}
    fund_totals: dict[str, float] = {}

    for d, snap in (state.get("daily") or {}).items():
        if d < cutoff or not isinstance(snap, dict):
            continue
        for row in snap.get("gain") or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("行业", "")).strip()
            if name:
                gain_totals[name] = gain_totals.get(name, 0.0) + _f(row.get("行业-涨跌幅"))
        for row in snap.get("fund") or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("行业", "")).strip()
            if name:
                fund_totals[name] = fund_totals.get(name, 0.0) + _f(row.get("净额"))

    return _max_by_total(gain_totals), _max_by_total(fund_totals)


def update_main_theme_state(payload: dict) -> dict[str, Any]:
    """按日写入概念榜快照（涨幅/资金各 top N），保留滑动窗口。"""
    cfg = _theme_cfg()
    limit = int(cfg.get("board_limit", 10))
    lookback = int(cfg.get("lookback_days", 5))
    today = _today()
    state = _load_state()
    daily: dict[str, Any] = state.setdefault("daily", {})

    if daily.get(today) is None:
        daily[today] = {
            "gain": _snapshot_gain_rows(payload, limit),
            "fund": _snapshot_fund_rows(payload, limit),
        }

    _trim_daily(state, lookback)
    gain, fund = snapshot_boards(payload, limit=limit)
    state["last_update_date"] = today
    state["today_gain"] = sorted(gain)
    state["today_fund"] = sorted(fund)
    state["today_dual"] = sorted(gain & fund)
    gain_main, fund_main = resolve_main_theme_leaders(state, lookback=lookback)
    state["gain_main"] = gain_main
    state["fund_main"] = fund_main
    _save_state(state)
    return state


def resolve_main_themes(payload: dict, *, update: bool = False) -> set[str]:
    """确认主线：近 N 日累计涨幅最大 + 累计资金流入最多，最多 2 条。

    ``update=True`` 时写入日快照（仅应由晚间复盘调用）；默认只读 ``main_themes.json``。
    """
    cfg = _theme_cfg()
    lookback = int(cfg.get("lookback_days", 5))
    state = update_main_theme_state(payload) if update else _load_state()
    gain_main, fund_main = resolve_main_theme_leaders(state, lookback=lookback)
    out: set[str] = set()
    if gain_main:
        out.add(gain_main)
    if fund_main:
        out.add(fund_main)
    return out


def concept_resonance_weights(payload: dict, *, update: bool = False) -> dict[str, float]:
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
            fund_net[n] = _f(row.get("净额"))
    return gain_rank, fund_rank, fund_net


def _rank_bonus(rank: int, limit: int, max_pts: float) -> float:
    if rank <= 0 or rank > limit:
        return 0.0
    return max_pts * (limit - rank + 1) / limit


def score_concept_resonance(
    stock_concepts: set[str],
    payload: dict,
    *,
    update: bool = False,
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


def theme_detail(payload: dict, *, update: bool = False) -> dict[str, Any]:
    """供评分维度输出的调试信息（默认不写入主线状态）。"""
    cfg = _theme_cfg()
    limit = int(cfg.get("board_limit", 10))
    lookback = int(cfg.get("lookback_days", 5))
    gain, fund = snapshot_boards(payload, limit=limit)
    main = resolve_main_themes(payload, update=update)
    state = _load_state()
    gain_main, fund_main = resolve_main_theme_leaders(state, lookback=lookback)
    return {
        "当日涨幅概念": sorted(gain),
        "当日资金概念": sorted(fund),
        "涨幅主线": gain_main,
        "资金主线": fund_main,
        "确认主线": sorted(main),
        "概念权重": concept_resonance_weights(payload, update=False),
    }
