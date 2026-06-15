"""概念板块跟踪：滑动窗口四榜快照 + 概念权重分 + 个股 concept_theme 打分。

评分概念池 = 过去 (lookback−1) 个交易日文件中的涨幅/资金榜概念 ∪ 当日 payload 涨幅/资金榜概念。
概念权重 = 近 lookback 个交易日（含当日）的上榜次数、综合涨幅、净流入，按配置权重合成 0–100。
日快照写入 concept_tracker.json 仅在 ``post_market_evening``；盘中/午间/盘前只读文件并叠加当日 payload。
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.utils.common_util import is_real_workday_cn
from quant.config import load_gates_config
from quant.store.paths import state_file

_SH_TZ = ZoneInfo("Asia/Shanghai")
_STATE_NAME = "concept_tracker.json"


def _concept_tracker_cfg() -> dict[str, Any]:
    gates = load_gates_config()
    return gates.get("concept_tracker") or {}


def _board_rows(payload: dict, key: str, limit: int) -> list[dict]:
    boards = payload.get("概念板块") or {}
    rows = boards.get(key) or []
    return [r for r in rows[:limit] if isinstance(r, dict)]


def _f(v: object, default: float = 0.0) -> float:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _row_net(row: dict[str, Any]) -> float:
    """概念单日资金净流入 = 净额，缺失时用流入资金 − 流出资金（亿元）。"""
    net = row.get("净额")
    if net is not None and str(net).strip() != "":
        return _f(net)
    return _f(row.get("流入资金")) - _f(row.get("流出资金"))


def _row_gain_chg(row: dict[str, Any]) -> float:
    return _f(row.get("行业-涨跌幅"))


def _today_date() -> date:
    return datetime.now(_SH_TZ).date()


def _trading_days_window(n: int, *, end: date | None = None) -> list[str]:
    """最近 n 个交易日（含 end 当日若为交易日），返回 ISO 日期升序。"""
    ref = end or _today_date()
    out: list[str] = []
    d = ref
    guard = 0
    while len(out) < n and guard < n * 4 + 30:
        guard += 1
        if is_real_workday_cn(d):
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return sorted(out)


def _past_board_trading_days(n: int) -> list[str]:
    """today 之前的最近 n 个交易日（升序）。"""
    out: list[str] = []
    d = _today_date() - timedelta(days=1)
    guard = 0
    while len(out) < n and guard < n * 4 + 30:
        guard += 1
        if is_real_workday_cn(d):
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return sorted(out)


def _lookback_trading_days(cfg: dict[str, Any] | None = None) -> int:
    c = cfg or _concept_tracker_cfg()
    return max(1, int(c.get("lookback_days", 10)))


def _past_board_days(cfg: dict[str, Any] | None = None) -> int:
    """评分池：文件侧覆盖的过去交易日数（默认 lookback−1，与「当日 payload」合计约 lookback 日）。"""
    c = cfg or _concept_tracker_cfg()
    if c.get("past_board_days") is not None:
        return max(1, int(c["past_board_days"]))
    return max(1, _lookback_trading_days(c) - 1)


def _trim_daily(state: dict[str, Any], lookback_trading: int) -> None:
    """保留滑动窗口所需日历范围（约为交易日数 × 2）。"""
    daily: dict[str, Any] = state.setdefault("daily", {})
    buffer_days = lookback_trading * 2 + 5
    cutoff = (_today_date() - timedelta(days=buffer_days)).isoformat()
    for key in list(daily.keys()):
        if key < cutoff:
            del daily[key]


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
        out.append(
            {
                "行业": name,
                "行业-涨跌幅": _row_gain_chg(row),
                "净额": _row_net(row),
            }
        )
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
        out.append(
            {
                "行业": name,
                "净额": _row_net(row),
                "行业-涨跌幅": _row_gain_chg(row),
            }
        )
    return out


def _snapshot_fund_out_rows(payload: dict, limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in _board_rows(payload, "资金流出榜", limit):
        name = str(row.get("行业", "")).strip()
        if not name:
            continue
        out.append(
            {
                "行业": name,
                "净额": _row_net(row),
                "行业-涨跌幅": _row_gain_chg(row),
            }
        )
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


def _max_by_total(totals: dict[str, float]) -> str | None:
    if not totals:
        return None
    return max(totals.items(), key=lambda x: (x[1], x[0]))[0]


def _daily_snap(snap: dict) -> dict[str, list[dict[str, Any]]]:
    return {
        "gain": [r for r in (snap.get("gain") or []) if isinstance(r, dict)],
        "loss": [r for r in (snap.get("loss") or []) if isinstance(r, dict)],
        "fund": [r for r in (snap.get("fund") or []) if isinstance(r, dict)],
        "fund_out": [r for r in (snap.get("fund_out") or []) if isinstance(r, dict)],
    }


def _concept_net_from_row(row: dict[str, Any]) -> float:
    if row.get("净额") is not None and str(row.get("净额")).strip() != "":
        return _f(row.get("净额"))
    return _row_net(row)


def _board_concept_names_from_snap(snap: dict) -> set[str]:
    """单日快照中涨幅榜 + 资金流入榜的概念名。"""
    boards = _daily_snap(snap)
    names: set[str] = set()
    for row in boards["gain"]:
        n = str(row.get("行业", "")).strip()
        if n:
            names.add(n)
    for row in boards["fund"]:
        n = str(row.get("行业", "")).strip()
        if n:
            names.add(n)
    return names


def _snap_from_payload(payload: dict, limit: int) -> dict[str, list[dict[str, Any]]]:
    return {
        "gain": _snapshot_gain_rows(payload, limit),
        "loss": _snapshot_loss_rows(payload, limit),
        "fund": _snapshot_fund_rows(payload, limit),
        "fund_out": _snapshot_fund_out_rows(payload, limit),
    }


def _payload_has_boards(payload: dict | None, limit: int) -> bool:
    if not payload:
        return False
    return bool(_board_rows(payload, "涨幅榜", limit) or _board_rows(payload, "资金流入榜", limit))


def _resolve_day_snap(
    day: str,
    daily: dict[str, Any],
    payload: dict | None,
    limit: int,
) -> dict | None:
    """单日四榜快照：当日优先 payload（有榜时），否则读文件。"""
    today_str = _today()
    if day == today_str:
        if _payload_has_boards(payload, limit):
            return _snap_from_payload(payload or {}, limit)
        raw = daily.get(day)
        return raw if isinstance(raw, dict) else None
    raw = daily.get(day)
    return raw if isinstance(raw, dict) else None


def _today_gain_fund_concepts(
    payload: dict,
    daily: dict[str, Any],
    *,
    limit: int,
) -> tuple[set[str], set[str]]:
    """当日涨幅榜 / 资金榜概念：优先 payload，无榜时回退文件当日快照。"""
    if _payload_has_boards(payload, limit):
        return snapshot_boards(payload, limit=limit)
    today_snap = daily.get(_today())
    if isinstance(today_snap, dict):
        boards = _daily_snap(today_snap)
        gain: set[str] = set()
        fund: set[str] = set()
        for row in boards["gain"]:
            n = str(row.get("行业", "")).strip()
            if n:
                gain.add(n)
        for row in boards["fund"]:
            n = str(row.get("行业", "")).strip()
            if n:
                fund.add(n)
        return gain, fund
    return set(), set()


def _today_board_concepts(
    payload: dict,
    daily: dict[str, Any],
    *,
    limit: int,
) -> set[str]:
    """当日涨幅/资金榜概念：优先 payload，无榜时回退文件当日快照（晚间复盘已写入后）。"""
    gain, fund = _today_gain_fund_concepts(payload, daily, limit=limit)
    return gain | fund


def _accumulate_day_metrics(
    snap: dict,
    *,
    selection_count: dict[str, int],
    composite_gain: dict[str, float],
    net_fund: dict[str, float],
) -> None:
    boards = _daily_snap(snap)
    daily_net: dict[str, float] = {}
    daily_gain_done: set[str] = set()

    for row in boards["gain"]:
        name = str(row.get("行业", "")).strip()
        if not name:
            continue
        selection_count[name] += 1
        composite_gain[name] += _row_gain_chg(row)
        daily_gain_done.add(name)
        daily_net[name] = _concept_net_from_row(row)

    for key in ("fund", "fund_out"):
        for row in boards[key]:
            name = str(row.get("行业", "")).strip()
            if not name:
                continue
            if name not in daily_gain_done:
                chg = _row_gain_chg(row)
                if chg != 0.0 or row.get("行业-涨跌幅") is not None:
                    composite_gain[name] += chg
                    daily_gain_done.add(name)
            if name in daily_net:
                continue
            daily_net[name] = _concept_net_from_row(row)

    for name, net in daily_net.items():
        net_fund[name] += net


def collect_concept_window_metrics(
    state: dict[str, Any] | None = None,
    payload: dict | None = None,
    *,
    lookback: int | None = None,
) -> dict[str, dict[str, float]]:
    """近 N 个交易日概念原始指标：入选次数、综合涨幅(%)、资金净流入(亿元)；当日优先读 payload。"""
    cfg = _concept_tracker_cfg()
    window = lookback if lookback is not None else _lookback_trading_days(cfg)
    st = state if state is not None else _load_state()
    window_dates = set(_trading_days_window(window))
    daily: dict[str, Any] = st.get("daily") or {}
    today_str = _today()
    limit = int(cfg.get("board_limit", 10))

    selection_count: dict[str, int] = defaultdict(int)
    composite_gain: dict[str, float] = defaultdict(float)
    net_fund: dict[str, float] = defaultdict(float)

    for d in sorted(window_dates):
        snap = _resolve_day_snap(d, daily, payload, limit)
        if snap is None:
            continue
        _accumulate_day_metrics(
            snap,
            selection_count=selection_count,
            composite_gain=composite_gain,
            net_fund=net_fund,
        )

    universe = set(selection_count) | set(composite_gain) | set(net_fund)
    return {
        name: {
            "入选次数": float(selection_count.get(name, 0)),
            "综合涨幅": composite_gain.get(name, 0.0),
            "资金净流入": net_fund.get(name, 0.0),
        }
        for name in universe
    }


def build_scoring_concept_pool(
    payload: dict,
    state: dict[str, Any] | None = None,
) -> set[str]:
    """评分概念池：过去 past_board_days 文件榜 ∪ 当日 payload 涨幅/资金榜。"""
    cfg = _concept_tracker_cfg()
    st = state if state is not None else _load_state()
    past_n = _past_board_days(cfg)
    limit = int(cfg.get("board_limit", 10))
    daily: dict[str, Any] = st.get("daily") or {}

    names: set[str] = set()
    for d in _past_board_trading_days(past_n):
        snap = daily.get(d)
        if isinstance(snap, dict):
            names.update(_board_concept_names_from_snap(snap))

    names.update(_today_board_concepts(payload, daily, limit=limit))
    return names


def _normalize_metric(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    mn = min(values.values())
    mx = max(values.values())
    if mx == mn:
        return {k: 50.0 for k in values}
    span = mx - mn
    return {k: 100.0 * (v - mn) / span for k, v in values.items()}


def _weight_cfg(cfg: dict[str, Any] | None = None) -> tuple[float, float, float, float]:
    c = cfg or _concept_tracker_cfg()
    w_cfg = c.get("score_weights") or {}
    w_count = float(w_cfg.get("selection_count", 50))
    w_gain = float(w_cfg.get("composite_gain", 30))
    w_fund = float(w_cfg.get("net_fund_flow", 20))
    weight_sum = w_count + w_gain + w_fund
    if weight_sum <= 0:
        weight_sum = 100.0
    return w_count, w_gain, w_fund, weight_sum


def _scores_from_metrics(
    metrics: dict[str, dict[str, float]],
    *,
    cfg: dict[str, Any] | None = None,
) -> dict[str, float]:
    """由概念原始指标计算 0–100 权重分。"""
    if not metrics:
        return {}
    w_count, w_gain, w_fund, weight_sum = _weight_cfg(cfg)
    count_vals = {k: v["入选次数"] for k, v in metrics.items()}
    gain_vals = {k: v["综合涨幅"] for k, v in metrics.items()}
    fund_vals = {k: v["资金净流入"] for k, v in metrics.items()}
    norm_count = _normalize_metric(count_vals)
    norm_gain = _normalize_metric(gain_vals)
    norm_fund = _normalize_metric(fund_vals)
    scores: dict[str, float] = {}
    for name in metrics:
        scores[name] = (
            w_count * norm_count.get(name, 50.0)
            + w_gain * norm_gain.get(name, 50.0)
            + w_fund * norm_fund.get(name, 50.0)
        ) / weight_sum
    return scores


def build_scoring_concept_scores(
    payload: dict,
    state: dict[str, Any] | None = None,
) -> dict[str, float]:
    """统一评分：池内概念按近 lookback 日指标（含当日 payload）加权。"""
    cfg = _concept_tracker_cfg()
    st = state if state is not None else _load_state()
    pool = build_scoring_concept_pool(payload, st)
    if not pool:
        return {}

    lookback = _lookback_trading_days(cfg)
    all_metrics = collect_concept_window_metrics(st, payload, lookback=lookback)
    empty = {"入选次数": 0.0, "综合涨幅": 0.0, "资金净流入": 0.0}
    pool_metrics = {name: all_metrics.get(name, empty) for name in pool}
    return _scores_from_metrics(pool_metrics, cfg=cfg)


def build_concept_net_scores(
    state: dict[str, Any] | None = None,
    payload: dict | None = None,
    *,
    lookback: int | None = None,
) -> dict[str, float]:
    """近 N 日全量概念权重（调试/摘要）；评分请用 build_scoring_concept_scores。"""
    cfg = _concept_tracker_cfg()
    window = lookback if lookback is not None else _lookback_trading_days(cfg)
    metrics = collect_concept_window_metrics(state, payload, lookback=window)
    return _scores_from_metrics(metrics, cfg=cfg)


def resolve_window_leading_concepts(
    state: dict[str, Any],
    payload: dict | None = None,
    *,
    lookback: int | None = None,
) -> tuple[str | None, str | None]:
    """滑动窗口内：综合涨幅最大、资金净流入最大概念（各 1 条，供调试/摘要）。"""
    metrics = collect_concept_window_metrics(state, payload, lookback=lookback)
    if not metrics:
        return None, None
    gain_totals = {k: v["综合涨幅"] for k, v in metrics.items()}
    fund_totals = {k: v["资金净流入"] for k, v in metrics.items()}
    return _max_by_total(gain_totals), _max_by_total(fund_totals)


def update_concept_tracker_state(payload: dict) -> dict[str, Any]:
    """按日写入概念四榜快照，保留滑动窗口。"""
    cfg = _concept_tracker_cfg()
    limit = int(cfg.get("board_limit", 10))
    lookback = _lookback_trading_days(cfg)
    today = _today()
    state = _load_state()
    daily: dict[str, Any] = state.setdefault("daily", {})

    if daily.get(today) is None:
        daily[today] = _snap_from_payload(payload, limit)

    _trim_daily(state, lookback)
    gain, fund = snapshot_boards(payload, limit=limit)
    state["last_update_date"] = today
    state["today_gain"] = sorted(gain)
    state["today_fund"] = sorted(fund)
    state["today_dual"] = sorted(gain & fund)
    leading_gain, leading_fund = resolve_window_leading_concepts(state, payload, lookback=lookback)
    state["leading_gain_concept"] = leading_gain
    state["leading_fund_concept"] = leading_fund
    _save_state(state)
    return state


def _concept_rank_map(nets: dict[str, float]) -> dict[str, int]:
    ranked = sorted(nets.items(), key=lambda x: (-x[1], x[0]))
    return {name: i + 1 for i, (name, _) in enumerate(ranked)}


def _top_n_concept_names(nets: dict[str, float], top_n: int) -> list[str]:
    ranked = sorted(nets.items(), key=lambda x: (-x[1], x[0]))
    return [name for name, _ in ranked[: max(1, top_n)]]


def _rank_tier_cfg(cfg: dict[str, Any] | None = None) -> dict[str, float]:
    c = cfg or _concept_tracker_cfg()
    tiers = c.get("rank_tiers") or {}
    return {
        "mid_score": float(tiers.get("mid_score", 45)),
        "low_score": float(tiers.get("low_score", 5)),
        "off_top_penalty": float(tiers.get("off_top_penalty", -50)),
    }


def _score_by_hit_rank(rank: int | None, raw_score: float, cfg: dict[str, Any] | None = None) -> tuple[float, str]:
    """按命中概念在窗口内的排名分档给分。"""
    tiers = _rank_tier_cfg(cfg)
    if rank is None or rank <= 0:
        return tiers["off_top_penalty"], "榜外"
    if rank <= 3:
        return max(-100.0, min(100.0, raw_score)), "前三"
    if rank <= 7:
        return tiers["mid_score"], "中游(4-7)"
    if rank <= 10:
        return tiers["low_score"], "边缘(8-10)"
    return tiers["off_top_penalty"], "榜外(11+)"


def score_concept_resonance(
    stock_concepts: set[str],
    payload: dict,
    *,
    mode: str = "",
    update: bool = False,
) -> tuple[float, dict[str, Any]]:
    """个股 concept_theme：按命中概念的最佳窗口排名分档给分。"""
    del mode, update
    cfg = _concept_tracker_cfg()
    w_cfg = cfg.get("score_weights") or {}
    no_hit = float(w_cfg.get("no_hit", 0))
    tiers = _rank_tier_cfg(cfg)

    state = _load_state()
    daily: dict[str, Any] = state.get("daily") or {}
    limit = int(cfg.get("board_limit", 10))
    pool = build_scoring_concept_pool(payload, state)
    nets = build_scoring_concept_scores(payload, state)
    if not nets:
        return no_hit, {"available": False}

    gain, fund = _today_gain_fund_concepts(payload, daily, limit=limit)
    past_dates = _past_board_trading_days(_past_board_days(cfg))
    rank_map = _concept_rank_map(nets)
    top_names = _top_n_concept_names(nets, 10)

    base_detail: dict[str, Any] = {
        "available": True,
        "评分概念池": sorted(pool),
        "当日榜概念": sorted(gain | fund),
        "文件榜覆盖日": past_dates,
        "窗口前十概念": top_names,
        "排名分档": tiers,
    }

    matched = {c: nets[c] for c in stock_concepts if c in nets}
    if not matched:
        return no_hit, {
            **base_detail,
            "命中概念": [],
            "概念权重分": {},
            "排名档位": None,
        }

    best_name = min(matched.keys(), key=lambda c: (rank_map.get(c, 9999), -matched[c], c))
    best_raw = matched[best_name]
    best_rank = rank_map.get(best_name)
    score, tier_label = _score_by_hit_rank(best_rank, best_raw, cfg)
    hit_detail = {
        k: round(v, 2) for k, v in sorted(matched.items(), key=lambda x: (-x[1], x[0]))
    }
    matched_ranks = {k: rank_map.get(k) for k in sorted(matched.keys())}

    return score, {
        **base_detail,
        "命中概念": sorted(matched.keys()),
        "概念权重分": hit_detail,
        "命中概念排名": matched_ranks,
        "最佳命中概念": best_name,
        "最高概念分": round(best_raw, 2),
        "最高命中排名": best_rank,
        "排名档位": tier_label,
        "概念减分": score < 0,
    }


def theme_detail(payload: dict, *, mode: str = "", update: bool = False) -> dict[str, Any]:
    """供评分维度输出的调试信息。"""
    del mode, update
    cfg = _concept_tracker_cfg()
    limit = int(cfg.get("board_limit", 10))
    lookback = _lookback_trading_days(cfg)
    state = _load_state()
    daily: dict[str, Any] = state.get("daily") or {}
    gain, fund = _today_gain_fund_concepts(payload, daily, limit=limit)
    pool = build_scoring_concept_pool(payload, state)
    leading_gain, leading_fund = resolve_window_leading_concepts(state, payload, lookback=lookback)
    metrics = collect_concept_window_metrics(state, payload, lookback=lookback)
    nets = build_scoring_concept_scores(payload, state)
    top = sorted(nets.items(), key=lambda x: -x[1])[:8]
    return {
        "当日涨幅概念": sorted(gain),
        "当日资金概念": sorted(fund),
        "窗口涨幅领先": leading_gain,
        "窗口资金领先": leading_fund,
        "评分概念池": sorted(pool),
        "文件榜覆盖日": _past_board_trading_days(_past_board_days(cfg)),
        "概念权重分": {k: round(v, 2) for k, v in nets.items()},
        "概念窗口指标": {
            name: {
                "入选次数": int(metrics[name]["入选次数"]),
                "综合涨幅": round(metrics[name]["综合涨幅"], 2),
                "资金净流入": round(metrics[name]["资金净流入"], 2),
            }
            for name in sorted(
                (n for n in pool if n in metrics),
                key=lambda n: -nets.get(n, 0),
            )[:12]
        },
        "权重分靠前": top,
    }
