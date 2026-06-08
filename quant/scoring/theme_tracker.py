"""主线题材：滑动窗口概念榜快照 + 概念净分（涨/跌、流入/流出）+ 个股 concept_theme 打分。

日快照与主线确认仅在 ``post_market_evening`` 写入 ``main_themes.json``；
盘中/午间/盘前等模式只读已确认主线，不更新状态。
"""

from __future__ import annotations

import json
from collections import defaultdict
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


def _snapshot_loss_rows(payload: dict, limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in _board_rows(payload, "跌幅榜", limit):
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


def _snapshot_fund_out_rows(payload: dict, limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in _board_rows(payload, "资金流出榜", limit):
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
    """按日写入概念四榜快照，保留滑动窗口。"""
    cfg = _theme_cfg()
    limit = int(cfg.get("board_limit", 10))
    lookback = int(cfg.get("lookback_days", 5))
    today = _today()
    state = _load_state()
    daily: dict[str, Any] = state.setdefault("daily", {})

    if daily.get(today) is None:
        daily[today] = {
            "gain": _snapshot_gain_rows(payload, limit),
            "loss": _snapshot_loss_rows(payload, limit),
            "fund": _snapshot_fund_rows(payload, limit),
            "fund_out": _snapshot_fund_out_rows(payload, limit),
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
    """确认主线：近 N 日累计涨幅最大 + 累计资金流入最多，最多 2 条。"""
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


def _rank_bonus(rank: int, limit: int, max_pts: float) -> float:
    if rank <= 0 or rank > limit:
        return 0.0
    return max_pts * (limit - rank + 1) / limit


def _daily_snap(snap: dict) -> dict[str, list[dict[str, Any]]]:
    return {
        "gain": [r for r in (snap.get("gain") or []) if isinstance(r, dict)],
        "loss": [r for r in (snap.get("loss") or []) if isinstance(r, dict)],
        "fund": [r for r in (snap.get("fund") or []) if isinstance(r, dict)],
        "fund_out": [r for r in (snap.get("fund_out") or []) if isinstance(r, dict)],
    }


def build_concept_net_scores(
    state: dict[str, Any] | None = None,
    *,
    lookback: int | None = None,
) -> dict[str, float]:
    """滑动窗口内各概念净倾向分：涨/流入加分，跌/流出减分；同概念对立榜自然对冲。"""
    cfg = _theme_cfg()
    limit = int(cfg.get("board_limit", 10))
    window = lookback if lookback is not None else int(cfg.get("lookback_days", 5))
    w_cfg = cfg.get("score_weights") or {}
    w_gain = float(w_cfg.get("gain_rank", 25))
    w_loss = float(w_cfg.get("loss_rank", 25))
    w_fund_in = float(w_cfg.get("fund_rank", 20))
    w_fund_out = float(w_cfg.get("fund_out_rank", 20))
    w_confirmed = float(w_cfg.get("confirmed", 40))

    st = state if state is not None else _load_state()
    cutoff = _window_start(window)
    scores: dict[str, float] = defaultdict(float)

    for d, snap in (st.get("daily") or {}).items():
        if d < cutoff or not isinstance(snap, dict):
            continue
        boards = _daily_snap(snap)
        for i, row in enumerate(boards["gain"]):
            name = str(row.get("行业", "")).strip()
            if name:
                scores[name] += _rank_bonus(i + 1, limit, w_gain)
        for i, row in enumerate(boards["loss"]):
            name = str(row.get("行业", "")).strip()
            if name:
                scores[name] -= _rank_bonus(i + 1, limit, w_loss)
        for i, row in enumerate(boards["fund"]):
            name = str(row.get("行业", "")).strip()
            if name:
                scores[name] += _rank_bonus(i + 1, limit, w_fund_in)
        for i, row in enumerate(boards["fund_out"]):
            name = str(row.get("行业", "")).strip()
            if name:
                scores[name] -= _rank_bonus(i + 1, limit, w_fund_out)

    gain_main, fund_main = resolve_main_theme_leaders(st, lookback=window)
    if gain_main:
        scores[gain_main] += w_confirmed
    if fund_main and fund_main != gain_main:
        scores[fund_main] += w_confirmed

    return dict(scores)


def max_concept_net_score(stock_concepts: set[str], payload: dict | None = None) -> float:
    """个股概念集合在窗口净分中的最高值（无匹配为 0）。"""
    del payload
    if not stock_concepts:
        return 0.0
    nets = build_concept_net_scores()
    matched = [nets[c] for c in stock_concepts if c in nets]
    return max(matched) if matched else 0.0


def concept_resonance_weights(payload: dict, *, update: bool = False) -> dict[str, float]:
    """兼容旧引用：返回滑动窗口概念净分。"""
    del payload, update
    return build_concept_net_scores()


def score_concept_resonance(
    stock_concepts: set[str],
    payload: dict,
    *,
    update: bool = False,
) -> tuple[float, dict[str, Any]]:
    """个股 concept_theme：最强正向概念加分 + 最强负向概念减分（中性 50）。"""
    del payload, update
    cfg = _theme_cfg()
    w_cfg = cfg.get("score_weights") or {}
    neutral = float(w_cfg.get("neutral", 50))
    w_pos = float(w_cfg.get("stock_pos_weight", 0.6))
    w_neg = float(w_cfg.get("stock_neg_weight", 0.3))

    nets = build_concept_net_scores()
    if not nets:
        return neutral, {"available": False}

    matched = {c: nets[c] for c in stock_concepts if c in nets}
    if not matched:
        return neutral, {"命中概念": [], "概念净分": {}, "available": True}

    pos_vals = [v for v in matched.values() if v > 0]
    neg_vals = [v for v in matched.values() if v < 0]
    pos_peak = max(pos_vals) if pos_vals else 0.0
    neg_trough = min(neg_vals) if neg_vals else 0.0

    raw = neutral + w_pos * pos_peak + w_neg * neg_trough
    score = max(0.0, min(100.0, raw))
    return score, {
        "available": True,
        "命中概念": sorted(matched.keys()),
        "概念净分": {k: round(v, 2) for k, v in sorted(matched.items(), key=lambda x: -x[1])},
        "正向峰值": round(pos_peak, 2),
        "负向峰值": round(neg_trough, 2),
    }


def theme_detail(payload: dict, *, update: bool = False) -> dict[str, Any]:
    """供评分维度输出的调试信息（默认不写入主线状态）。"""
    cfg = _theme_cfg()
    limit = int(cfg.get("board_limit", 10))
    lookback = int(cfg.get("lookback_days", 5))
    gain, fund = snapshot_boards(payload, limit=limit)
    main = resolve_main_themes(payload, update=update)
    state = _load_state()
    gain_main, fund_main = resolve_main_theme_leaders(state, lookback=lookback)
    nets = build_concept_net_scores(state, lookback=lookback)
    top_pos = sorted(nets.items(), key=lambda x: -x[1])[:8]
    top_neg = sorted(nets.items(), key=lambda x: x[1])[:8]
    return {
        "当日涨幅概念": sorted(gain),
        "当日资金概念": sorted(fund),
        "涨幅主线": gain_main,
        "资金主线": fund_main,
        "确认主线": sorted(main),
        "概念净分": {k: round(v, 2) for k, v in nets.items()},
        "净分靠前": top_pos,
        "净分靠后": top_neg,
    }
