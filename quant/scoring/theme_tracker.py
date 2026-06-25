"""概念板块跟踪：滑动窗口四榜快照 + 概念权重分 + 个股 concept_theme 打分。

评分概念池 = 过去 (lookback−1) 个交易日文件中的涨幅/资金榜概念 ∪ 当日 payload 涨幅/资金榜概念。
概念权重 = 结构窗口(默认10日)与动量窗口(默认3日)按权重合成，或单窗口；含时间衰减与资金 log 缩放。
日快照写入 concept_tracker.json 仅在 ``post_market_evening``；盘中/午间/盘前只读文件并叠加当日 payload。
"""

from __future__ import annotations

import copy
import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.utils.common_util import is_real_workday_cn
from quant.config import load_gates_config
from quant.scoring.theme_boards import (
    BOARD_CONCEPT,
    BOARD_INDUSTRY,
    board_gain_fund_lists,
    section_board_rows,
)
from quant.store.paths import state_file

_SH_TZ = ZoneInfo("Asia/Shanghai")
_STATE_NAME = "concept_tracker.json"


def _concept_tracker_cfg(mode: str = "") -> dict[str, Any]:
    gates = load_gates_config()
    cfg = gates.get("concept_tracker") or {}
    if not mode:
        return cfg
    overrides = cfg.get("mode_overrides") or {}
    block = overrides.get(mode)
    if not isinstance(block, dict):
        return cfg
    merged = copy.deepcopy(cfg)
    for key, val in block.items():
        if key == "mode_overrides":
            continue
        if isinstance(val, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **val}
        else:
            merged[key] = copy.deepcopy(val)
    return merged


def _board_rows(
    payload: dict,
    key: str,
    limit: int,
    *,
    section: str = BOARD_CONCEPT,
) -> list[dict]:
    return section_board_rows(payload, section, key, limit=limit)


def _track_from_section(section: str) -> str:
    return "industry" if section == BOARD_INDUSTRY else "concept"


def _section_from_track(track: str) -> str:
    return BOARD_INDUSTRY if track == "industry" else BOARD_CONCEPT


def _f(v: object, default: float = 0.0) -> float:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _row_net(row: dict[str, Any]) -> float:
    """概念单日资金净流入 = 净额，缺失时用流入资金 − 流出资金（亿元）。"""
    net = row.get("净额")
    if net is None:
        net = row.get("净流入")
    if net is not None and str(net).strip() != "":
        return _f(net)
    return _f(row.get("流入资金")) - _f(row.get("流出资金"))


def _row_gain_chg(row: dict[str, Any]) -> float:
    chg = row.get("行业-涨跌幅")
    if chg is None:
        chg = row.get("涨跌幅")
    return _f(chg)


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


def snapshot_boards(
    payload: dict,
    *,
    limit: int = 10,
    section: str = BOARD_CONCEPT,
) -> tuple[set[str], set[str]]:
    """当日涨幅榜 / 资金流入榜名称（概念或行业单轨）。"""
    gain: set[str] = set()
    fund: set[str] = set()
    for row in _board_rows(payload, "涨幅榜", limit, section=section):
        n = str(row.get("行业", "")).strip()
        if n:
            gain.add(n)
    for row in _board_rows(payload, "资金流入榜", limit, section=section):
        n = str(row.get("行业", "")).strip()
        if n:
            fund.add(n)
    return gain, fund


def _snapshot_gain_rows(payload: dict, limit: int, *, section: str = BOARD_CONCEPT) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in _board_rows(payload, "涨幅榜", limit, section=section):
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


def _snapshot_loss_rows(payload: dict, limit: int, *, section: str = BOARD_CONCEPT) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in _board_rows(payload, "跌幅榜", limit, section=section):
        name = str(row.get("行业", "")).strip()
        if not name:
            continue
        out.append({"行业": name, "行业-涨跌幅": _f(row.get("行业-涨跌幅"))})
    return out


def _snapshot_fund_rows(payload: dict, limit: int, *, section: str = BOARD_CONCEPT) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in _board_rows(payload, "资金流入榜", limit, section=section):
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


def _snapshot_fund_out_rows(payload: dict, limit: int, *, section: str = BOARD_CONCEPT) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in _board_rows(payload, "资金流出榜", limit, section=section):
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


def _empty_track_snap() -> dict[str, list[dict[str, Any]]]:
    return {"gain": [], "loss": [], "fund": [], "fund_out": []}


def _track_snap_from_container(snap: dict, track: str) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(snap, dict):
        return _empty_track_snap()
    nested = snap.get(track)
    if isinstance(nested, dict):
        return _daily_snap(nested)
    if track == "industry":
        return _empty_track_snap()
    if "gain" in snap or "fund" in snap:
        return _daily_snap(snap)
    return _empty_track_snap()


def _board_names_from_snap(snap: dict, track: str) -> set[str]:
    """单日快照中涨幅榜 + 资金流入榜名称（概念轨或行业轨）。"""
    boards = _track_snap_from_container(snap, track)
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


def _snap_track_payload(payload: dict, limit: int, *, section: str) -> dict[str, list[dict[str, Any]]]:
    return {
        "gain": _snapshot_gain_rows(payload, limit, section=section),
        "loss": _snapshot_loss_rows(payload, limit, section=section),
        "fund": _snapshot_fund_rows(payload, limit, section=section),
        "fund_out": _snapshot_fund_out_rows(payload, limit, section=section),
    }


def _snap_from_payload(payload: dict, limit: int) -> dict[str, Any]:
    return {
        "concept": _snap_track_payload(payload, limit, section=BOARD_CONCEPT),
        "industry": _snap_track_payload(payload, limit, section=BOARD_INDUSTRY),
    }


def _payload_has_boards(
    payload: dict | None,
    limit: int,
    *,
    section: str = BOARD_CONCEPT,
) -> bool:
    if not payload:
        return False
    return bool(
        _board_rows(payload, "涨幅榜", limit, section=section)
        or _board_rows(payload, "资金流入榜", limit, section=section)
    )


def _resolve_day_snap(
    day: str,
    daily: dict[str, Any],
    payload: dict | None,
    limit: int,
    *,
    track: str,
) -> dict | None:
    """单日四榜快照（概念轨或行业轨）：当日优先 payload，否则读文件。"""
    section = _section_from_track(track)
    today_str = _today()
    if day == today_str:
        if _payload_has_boards(payload, limit, section=section):
            container = _snap_from_payload(payload or {}, limit)
            nested = container.get(track)
            return nested if isinstance(nested, dict) else None
        raw = daily.get(day)
        if isinstance(raw, dict):
            nested = raw.get(track)
            if isinstance(nested, dict):
                return nested
            if track == "concept" and ("gain" in raw or "fund" in raw):
                return {k: raw.get(k) or [] for k in ("gain", "loss", "fund", "fund_out")}
        return None
    raw = daily.get(day)
    if not isinstance(raw, dict):
        return None
    nested = raw.get(track)
    if isinstance(nested, dict):
        return nested
    if track == "concept" and ("gain" in raw or "fund" in raw):
        return {k: raw.get(k) or [] for k in ("gain", "loss", "fund", "fund_out")}
    return None


def _today_gain_fund_boards(
    payload: dict,
    daily: dict[str, Any],
    *,
    limit: int,
    section: str = BOARD_CONCEPT,
) -> tuple[set[str], set[str]]:
    """当日涨幅/资金榜：优先 payload，无榜时回退文件当日快照。"""
    if _payload_has_boards(payload, limit, section=section):
        return snapshot_boards(payload, limit=limit, section=section)
    track = _track_from_section(section)
    today_snap = daily.get(_today())
    if isinstance(today_snap, dict):
        boards = _track_snap_from_container(today_snap, track)
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


def _today_board_names(
    payload: dict,
    daily: dict[str, Any],
    *,
    limit: int,
    section: str = BOARD_CONCEPT,
) -> set[str]:
    gain, fund = _today_gain_fund_boards(payload, daily, limit=limit, section=section)
    return gain | fund


def _recency_decay(cfg: dict[str, Any] | None = None) -> float:
    """每日指标时间衰减；1.0 表示等权。越近权重越大。"""
    c = cfg or _concept_tracker_cfg()
    return min(1.0, max(0.1, float(c.get("recency_decay", 1.0))))


def _fund_log_scale(cfg: dict[str, Any] | None = None) -> bool:
    c = cfg or _concept_tracker_cfg()
    return bool(c.get("fund_log_scale", False))


def _day_recency_weight(day_index: int, total_days: int, decay: float) -> float:
    """day_index: 0=窗口最早 … total_days-1=最近（含当日）。"""
    if decay >= 0.999 or total_days <= 1:
        return 1.0
    days_ago = total_days - 1 - day_index
    return decay**days_ago


def _accumulate_day_metrics(
    snap: dict,
    *,
    selection_count: dict[str, int],
    composite_gain: dict[str, float],
    net_fund: dict[str, float],
    day_weight: float = 1.0,
) -> None:
    boards = _daily_snap(snap)
    daily_net: dict[str, float] = {}
    daily_gain_done: set[str] = set()
    daily_board_hit: set[str] = set()

    for row in boards["gain"]:
        name = str(row.get("行业", "")).strip()
        if not name:
            continue
        daily_board_hit.add(name)
        composite_gain[name] += _row_gain_chg(row) * day_weight
        daily_gain_done.add(name)
        daily_net[name] = _concept_net_from_row(row)

    for key in ("fund", "fund_out"):
        for row in boards[key]:
            name = str(row.get("行业", "")).strip()
            if not name:
                continue
            if key == "fund":
                daily_board_hit.add(name)
            if name not in daily_gain_done:
                chg = _row_gain_chg(row)
                if chg != 0.0 or row.get("行业-涨跌幅") is not None:
                    composite_gain[name] += chg * day_weight
                    daily_gain_done.add(name)
            if name in daily_net:
                continue
            daily_net[name] = _concept_net_from_row(row)

    for name in daily_board_hit:
        selection_count[name] += day_weight

    for name, net in daily_net.items():
        net_fund[name] += net * day_weight


def collect_concept_window_metrics(
    state: dict[str, Any] | None = None,
    payload: dict | None = None,
    *,
    lookback: int | None = None,
    section: str = BOARD_CONCEPT,
) -> dict[str, dict[str, float]]:
    """近 N 日单轨（概念或行业）原始指标。"""
    cfg = _concept_tracker_cfg()
    window = lookback if lookback is not None else _lookback_trading_days(cfg)
    st = state if state is not None else _load_state()
    window_dates = set(_trading_days_window(window))
    daily: dict[str, Any] = st.get("daily") or {}
    limit = int(cfg.get("board_limit", 10))
    track = _track_from_section(section)

    selection_count: dict[str, int] = defaultdict(int)
    composite_gain: dict[str, float] = defaultdict(float)
    net_fund: dict[str, float] = defaultdict(float)

    decay = _recency_decay(cfg)
    window_dates_sorted = sorted(window_dates)

    for i, d in enumerate(window_dates_sorted):
        snap = _resolve_day_snap(d, daily, payload, limit, track=track)
        if snap is None:
            continue
        w = _day_recency_weight(i, len(window_dates_sorted), decay)
        _accumulate_day_metrics(
            snap,
            selection_count=selection_count,
            composite_gain=composite_gain,
            net_fund=net_fund,
            day_weight=w,
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
    *,
    section: str = BOARD_CONCEPT,
) -> set[str]:
    """评分池：过去文件榜 ∪ 当日 payload（概念轨或行业轨，互不交叉）。"""
    cfg = _concept_tracker_cfg()
    st = state if state is not None else _load_state()
    past_n = _past_board_days(cfg)
    limit = int(cfg.get("board_limit", 10))
    daily: dict[str, Any] = st.get("daily") or {}
    track = _track_from_section(section)

    names: set[str] = set()
    for d in _past_board_trading_days(past_n):
        snap = daily.get(d)
        if isinstance(snap, dict):
            names.update(_board_names_from_snap(snap, track))

    names.update(_today_board_names(payload, daily, limit=limit, section=section))
    return names


def _normalize_mode(cfg: dict[str, Any] | None = None) -> str:
    c = cfg or _concept_tracker_cfg()
    mode = str(c.get("normalize_mode", "percentile")).strip().lower()
    return mode if mode in ("percentile", "minmax") else "percentile"


def _normalize_metric(
    values: dict[str, float],
    *,
    cfg: dict[str, Any] | None = None,
) -> dict[str, float]:
    if not values:
        return {}
    if _normalize_mode(cfg) == "minmax":
        mn = min(values.values())
        mx = max(values.values())
        if mx == mn:
            return {k: 50.0 for k in values}
        span = mx - mn
        return {k: 100.0 * (v - mn) / span for k, v in values.items()}

    sorted_items = sorted(values.items(), key=lambda x: (x[1], x[0]))
    n = len(sorted_items)
    if n == 1:
        return {k: 50.0 for k, _ in sorted_items}
    ranks: dict[str, float] = {}
    i = 0
    while i < n:
        j = i
        v = sorted_items[i][1]
        while j < n and sorted_items[j][1] == v:
            j += 1
        avg_rank = (i + j - 1) / 2.0
        pct = 100.0 * avg_rank / (n - 1)
        for k, _ in sorted_items[i:j]:
            ranks[k] = pct
        i = j
    return ranks


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


def _prepare_fund_values(fund_vals: dict[str, float], *, cfg: dict[str, Any] | None = None) -> dict[str, float]:
    """资金净流入归一化前预处理：可选 log1p 减弱 PCB 266 亿等极端值对 min-max 的挤压。"""
    if not _fund_log_scale(cfg):
        return fund_vals
    return {k: math.log1p(max(0.0, v)) for k, v in fund_vals.items()}


def _scores_from_metrics(
    metrics: dict[str, dict[str, float]],
    *,
    cfg: dict[str, Any] | None = None,
) -> dict[str, float]:
    """由概念原始指标计算 0–100 权重分。"""
    if not metrics:
        return {}
    c = cfg or _concept_tracker_cfg()
    w_count, w_gain, w_fund, weight_sum = _weight_cfg(c)
    count_vals = {k: v["入选次数"] for k, v in metrics.items()}
    gain_vals = {k: v["综合涨幅"] for k, v in metrics.items()}
    fund_vals = _prepare_fund_values({k: v["资金净流入"] for k, v in metrics.items()}, cfg=c)
    norm_count = _normalize_metric(count_vals, cfg=c)
    norm_gain = _normalize_metric(gain_vals, cfg=c)
    norm_fund = _normalize_metric(fund_vals, cfg=c)
    scores: dict[str, float] = {}
    for name in metrics:
        scores[name] = (
            w_count * norm_count.get(name, 50.0)
            + w_gain * norm_gain.get(name, 50.0)
            + w_fund * norm_fund.get(name, 50.0)
        ) / weight_sum
    return scores


def _dual_window_cfg(cfg: dict[str, Any] | None = None, *, mode: str = "") -> dict[str, Any] | None:
    c = cfg if cfg is not None else _concept_tracker_cfg(mode)
    raw = c.get("dual_window") or {}
    if not bool(raw.get("enabled", False)):
        return None
    struct_days = int(raw.get("structural_days") or _lookback_trading_days(c))
    mom_days = max(1, int(raw.get("momentum_days", 3)))
    w_struct = float(raw.get("structural_weight", 0.6))
    w_mom = float(raw.get("momentum_weight", 0.4))
    weight_sum = w_struct + w_mom
    if weight_sum <= 0:
        w_struct, w_mom, weight_sum = 0.6, 0.4, 1.0
    return {
        "structural_days": struct_days,
        "momentum_days": mom_days,
        "structural_weight": w_struct / weight_sum,
        "momentum_weight": w_mom / weight_sum,
    }


def _empty_metrics() -> dict[str, float]:
    return {"入选次数": 0.0, "综合涨幅": 0.0, "资金净流入": 0.0}


def _scores_for_pool(
    pool: set[str],
    metrics: dict[str, dict[str, float]],
    *,
    cfg: dict[str, Any],
) -> dict[str, float]:
    empty = _empty_metrics()
    pool_metrics = {name: metrics.get(name, empty) for name in pool}
    return _scores_from_metrics(pool_metrics, cfg=cfg)


def _blend_dual_window_scores(
    pool: set[str],
    structural: dict[str, float],
    momentum: dict[str, float],
    dw: dict[str, Any],
) -> dict[str, float]:
    w_s = dw["structural_weight"]
    w_m = dw["momentum_weight"]
    return {
        name: w_s * structural.get(name, 50.0) + w_m * momentum.get(name, 50.0)
        for name in pool
    }


def build_scoring_concept_scores(
    payload: dict,
    state: dict[str, Any] | None = None,
    *,
    mode: str = "",
    section: str = BOARD_CONCEPT,
) -> dict[str, float]:
    """单轨权重分（概念或行业）。"""
    cfg = _concept_tracker_cfg(mode)
    st = state if state is not None else _load_state()
    pool = build_scoring_concept_pool(payload, st, section=section)
    if not pool:
        return {}

    dw = _dual_window_cfg(cfg, mode=mode)
    if dw is None:
        lookback = _lookback_trading_days(cfg)
        all_metrics = collect_concept_window_metrics(
            st, payload, lookback=lookback, section=section
        )
        return _scores_for_pool(pool, all_metrics, cfg=cfg)

    struct_metrics = collect_concept_window_metrics(
        st, payload, lookback=dw["structural_days"], section=section
    )
    mom_metrics = collect_concept_window_metrics(
        st, payload, lookback=dw["momentum_days"], section=section
    )
    struct_scores = _scores_for_pool(pool, struct_metrics, cfg=cfg)
    mom_scores = _scores_for_pool(pool, mom_metrics, cfg=cfg)
    return _blend_dual_window_scores(pool, struct_scores, mom_scores, dw)


def build_dual_window_score_breakdown(
    payload: dict,
    state: dict[str, Any] | None = None,
    *,
    mode: str = "",
    section: str = BOARD_CONCEPT,
) -> dict[str, Any] | None:
    """调试：返回结构/动量分及合成结果；未启用双窗口时 None。"""
    cfg = _concept_tracker_cfg(mode)
    dw = _dual_window_cfg(cfg, mode=mode)
    if dw is None:
        return None
    st = state if state is not None else _load_state()
    pool = build_scoring_concept_pool(payload, st, section=section)
    if not pool:
        return None
    struct_metrics = collect_concept_window_metrics(
        st, payload, lookback=dw["structural_days"], section=section
    )
    mom_metrics = collect_concept_window_metrics(
        st, payload, lookback=dw["momentum_days"], section=section
    )
    struct_scores = _scores_for_pool(pool, struct_metrics, cfg=cfg)
    mom_scores = _scores_for_pool(pool, mom_metrics, cfg=cfg)
    blended = _blend_dual_window_scores(pool, struct_scores, mom_scores, dw)
    top = sorted(blended.items(), key=lambda x: (-x[1], x[0]))[:8]
    return {
        "结构窗口日": dw["structural_days"],
        "动量窗口日": dw["momentum_days"],
        "结构权重": round(dw["structural_weight"], 2),
        "动量权重": round(dw["momentum_weight"], 2),
        "合成权重分": {k: round(v, 2) for k, v in blended.items()},
        "权重分靠前": [(n, round(s, 2)) for n, s in top],
    }


def build_concept_net_scores(
    state: dict[str, Any] | None = None,
    payload: dict | None = None,
    *,
    lookback: int | None = None,
    section: str = BOARD_CONCEPT,
) -> dict[str, float]:
    """近 N 日单轨全量权重（调试/摘要）。"""
    cfg = _concept_tracker_cfg()
    window = lookback if lookback is not None else _lookback_trading_days(cfg)
    metrics = collect_concept_window_metrics(state, payload, lookback=window, section=section)
    return _scores_from_metrics(metrics, cfg=cfg)


def resolve_window_leading_concepts(
    state: dict[str, Any],
    payload: dict | None = None,
    *,
    lookback: int | None = None,
    section: str = BOARD_CONCEPT,
) -> tuple[str | None, str | None]:
    """滑动窗口内：综合涨幅最大、资金净流入最大（单轨）。"""
    metrics = collect_concept_window_metrics(state, payload, lookback=lookback, section=section)
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
    gain_c, fund_c = snapshot_boards(payload, limit=limit, section=BOARD_CONCEPT)
    gain_i, fund_i = snapshot_boards(payload, limit=limit, section=BOARD_INDUSTRY)
    state["last_update_date"] = today
    state["today_gain"] = sorted(gain_c)
    state["today_fund"] = sorted(fund_c)
    state["today_dual"] = sorted(gain_c & fund_c)
    state["today_gain_industry"] = sorted(gain_i)
    state["today_fund_industry"] = sorted(fund_i)
    leading_gain, leading_fund = resolve_window_leading_concepts(
        state, payload, lookback=lookback, section=BOARD_CONCEPT
    )
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
        return max(-100.0, min(100.0, raw_score)), "中游(4-7)"
    if rank <= 10:
        return max(-100.0, min(100.0, raw_score)), "边缘(8-10)"
    return tiers["off_top_penalty"], "榜外(11+)"


def _fit_rank_cfg(cfg: dict[str, Any] | None = None) -> dict[str, float | int]:
    c = cfg or _concept_tracker_cfg()
    fit = c.get("fit_rank") or {}
    return {
        "weight_decay": float(fit.get("weight_decay", 0.85)),
        "max_concepts": int(fit.get("max_concepts", 15)),
    }


def _fit_rank_weight(fit_rank: int, cfg: dict[str, Any] | None = None) -> float:
    fit_cfg = _fit_rank_cfg(cfg)
    max_concepts = int(fit_cfg["max_concepts"])
    if fit_rank <= 0 or fit_rank > max_concepts:
        return 0.0
    decay = float(fit_cfg["weight_decay"])
    return decay ** (fit_rank - 1)


def _score_fit_rank_weighted_resonance(
    fit_order: list[tuple[str, int]],
    matched: dict[str, float],
    rank_map: dict[str, int],
    cfg: dict[str, Any],
) -> tuple[float, dict[str, Any], str, int | None, str]:
    """按概念粘合度加权汇总窗口榜命中分（粘合度越靠前权重越大）。"""
    hits: list[dict[str, Any]] = []
    for concept, fit_rank in fit_order:
        weight = _fit_rank_weight(fit_rank, cfg)
        if weight <= 0 or concept not in matched:
            continue
        board_rank = rank_map.get(concept)
        raw = matched[concept]
        board_score, tier = _score_by_hit_rank(board_rank, raw, cfg)
        hits.append(
            {
                "concept": concept,
                "fit_rank": fit_rank,
                "board_rank": board_rank,
                "raw_score": raw,
                "board_score": board_score,
                "tier": tier,
                "weight": weight,
                "weighted_score": board_score * weight,
            }
        )

    if not hits:
        tiers = _rank_tier_cfg(cfg)
        return float((cfg.get("score_weights") or {}).get("no_hit", 0)), {}, "", None, ""

    total_w = sum(float(h["weight"]) for h in hits)
    score = sum(float(h["weighted_score"]) for h in hits) / total_w
    primary = min(
        hits,
        key=lambda h: (int(h["fit_rank"]), int(h["board_rank"] or 9999), str(h["concept"])),
    )
    detail = {
        "概念粘合度评分": [
            {
                "概念": h["concept"],
                "粘合度": h["fit_rank"],
                "窗口排名": h["board_rank"],
                "权重分": round(float(h["raw_score"]), 2),
                "档位": h["tier"],
                "权重": round(float(h["weight"]), 4),
                "加权分": round(float(h["weighted_score"]), 2),
            }
            for h in hits
        ],
        "粘合度加权分": round(score, 2),
    }
    return (
        score,
        detail,
        str(primary["concept"]),
        primary["board_rank"],
        str(primary["tier"]),
    )


def _score_single_track_resonance(
    tags: set[str],
    payload: dict,
    *,
    section: str,
    track_label: str,
    mode: str = "",
    concept_fit_order: list[tuple[str, int]] | None = None,
) -> tuple[float, dict[str, Any]]:
    cfg = _concept_tracker_cfg(mode)
    w_cfg = cfg.get("score_weights") or {}
    no_hit = float(w_cfg.get("no_hit", 0))
    tiers = _rank_tier_cfg(cfg)

    state = _load_state()
    daily: dict[str, Any] = state.get("daily") or {}
    limit = int(cfg.get("board_limit", 10))
    pool = build_scoring_concept_pool(payload, state, section=section)
    nets = build_scoring_concept_scores(payload, state, mode=mode, section=section)
    if not nets:
        return no_hit, {"available": False, "赛道": track_label}

    gain, fund = _today_gain_fund_boards(payload, daily, limit=limit, section=section)
    past_dates = _past_board_trading_days(_past_board_days(cfg))
    rank_map = _concept_rank_map(nets)
    top_names = _top_n_concept_names(nets, 10)

    base_detail: dict[str, Any] = {
        "available": True,
        "赛道": track_label,
        f"评分{track_label}池": sorted(pool),
        f"当日榜{track_label}": sorted(gain | fund),
        "文件榜覆盖日": past_dates,
        f"窗口前十{track_label}": top_names,
        "排名分档": tiers,
    }

    matched = {c: nets[c] for c in tags if c in nets}
    if not matched:
        return no_hit, {
            **base_detail,
            f"命中{track_label}": [],
            f"{track_label}权重分": {},
            "排名档位": None,
        }

    use_fit_rank = (
        section == BOARD_CONCEPT
        and track_label == "概念"
        and concept_fit_order
    )
    fit_detail: dict[str, Any] = {}
    if use_fit_rank:
        score, fit_detail, best_name, best_rank, tier_label = _score_fit_rank_weighted_resonance(
            concept_fit_order,
            matched,
            rank_map,
            cfg,
        )
        if not best_name:
            use_fit_rank = False

    if not use_fit_rank:
        best_name = min(matched.keys(), key=lambda c: (rank_map.get(c, 9999), -matched[c], c))
        best_raw = matched[best_name]
        best_rank = rank_map.get(best_name)
        score, tier_label = _score_by_hit_rank(best_rank, best_raw, cfg)
        fit_detail = {}

    best_raw = matched.get(best_name, 0.0)
    hit_detail = {
        k: round(v, 2) for k, v in sorted(matched.items(), key=lambda x: (-x[1], x[0]))
    }
    matched_ranks = {k: rank_map.get(k) for k in sorted(matched.keys())}

    return score, {
        **base_detail,
        **fit_detail,
        f"命中{track_label}": sorted(matched.keys()),
        f"{track_label}权重分": hit_detail,
        f"命中{track_label}排名": matched_ranks,
        f"最佳命中{track_label}": best_name,
        f"最高{track_label}分": round(best_raw, 2),
        "最高命中排名": best_rank,
        "排名档位": tier_label,
        f"{track_label}减分": score < 0,
        "评分模式": "粘合度加权" if use_fit_rank else "窗口最优",
    }


def score_theme_resonance(
    stock_concepts: set[str],
    stock_industries: set[str],
    payload: dict,
    *,
    concept_fit_order: list[tuple[str, int]] | None = None,
    mode: str = "",
    update: bool = False,
) -> tuple[float, dict[str, Any]]:
    """概念轨 + 行业轨分轨匹配，取较高分（概念与行业不做交叉映射）。"""
    del update
    concept_score, concept_detail = _score_single_track_resonance(
        stock_concepts,
        payload,
        section=BOARD_CONCEPT,
        track_label="概念",
        mode=mode,
        concept_fit_order=concept_fit_order,
    )
    industry_score, industry_detail = _score_single_track_resonance(
        stock_industries,
        payload,
        section=BOARD_INDUSTRY,
        track_label="行业",
        mode=mode,
    )

    candidates: list[tuple[str, float, dict[str, Any]]] = []
    if concept_detail.get("命中概念"):
        candidates.append(("概念", concept_score, concept_detail))
    if industry_detail.get("命中行业"):
        candidates.append(("行业", industry_score, industry_detail))

    if not concept_detail.get("available") and not industry_detail.get("available"):
        return float(
            (_concept_tracker_cfg(mode).get("score_weights") or {}).get("no_hit", 0)
        ), {"available": False}

    if not candidates:
        no_hit = float(
            (_concept_tracker_cfg(mode).get("score_weights") or {}).get("no_hit", 0)
        )
        return no_hit, {
            "available": True,
            "最佳赛道": None,
            "概念共振": concept_detail,
            "行业共振": industry_detail,
            "命中概念": [],
        }

    best_track, score, best_detail = max(candidates, key=lambda x: (x[1], x[0]))

    return score, {
        **best_detail,
        "最佳赛道": best_track,
        "概念共振": concept_detail,
        "行业共振": industry_detail,
        "命中概念": concept_detail.get("命中概念") or [],
        "最佳命中概念": best_detail.get(f"最佳命中{best_track}"),
        "概念减分": score < 0,
    }


def score_concept_resonance(
    stock_concepts: set[str],
    payload: dict,
    *,
    concept_fit_order: list[tuple[str, int]] | None = None,
    mode: str = "",
    update: bool = False,
) -> tuple[float, dict[str, Any]]:
    """兼容旧调用：仅概念轨。"""
    return score_theme_resonance(
        stock_concepts,
        set(),
        payload,
        concept_fit_order=concept_fit_order,
        mode=mode,
        update=update,
    )


def theme_detail(payload: dict, *, mode: str = "", update: bool = False) -> dict[str, Any]:
    """供评分维度输出的调试信息。"""
    del update
    cfg = _concept_tracker_cfg(mode)
    limit = int(cfg.get("board_limit", 10))
    lookback = _lookback_trading_days(cfg)
    state = _load_state()
    daily: dict[str, Any] = state.get("daily") or {}
    concept_gain, concept_fund = board_gain_fund_lists(payload, BOARD_CONCEPT, limit=limit)
    industry_gain, industry_fund = board_gain_fund_lists(payload, BOARD_INDUSTRY, limit=limit)
    concept_pool = build_scoring_concept_pool(payload, state, section=BOARD_CONCEPT)
    industry_pool = build_scoring_concept_pool(payload, state, section=BOARD_INDUSTRY)
    leading_gain, leading_fund = resolve_window_leading_concepts(
        state, payload, lookback=lookback, section=BOARD_CONCEPT
    )
    concept_metrics = collect_concept_window_metrics(
        state, payload, lookback=lookback, section=BOARD_CONCEPT
    )
    industry_metrics = collect_concept_window_metrics(
        state, payload, lookback=lookback, section=BOARD_INDUSTRY
    )
    concept_nets = build_scoring_concept_scores(payload, state, mode=mode, section=BOARD_CONCEPT)
    industry_nets = build_scoring_concept_scores(payload, state, mode=mode, section=BOARD_INDUSTRY)
    top = sorted(concept_nets.items(), key=lambda x: (-x[1], x[0]))[:8]
    out: dict[str, Any] = {
        "当日涨幅概念": sorted(concept_gain),
        "当日资金概念": sorted(concept_fund),
        "当日涨幅行业": sorted(industry_gain),
        "当日资金行业": sorted(industry_fund),
        "窗口涨幅领先": leading_gain,
        "窗口资金领先": leading_fund,
        "评分概念池": sorted(concept_pool),
        "评分行业池": sorted(industry_pool),
        "文件榜覆盖日": _past_board_trading_days(_past_board_days(cfg)),
        "概念权重分": {k: round(v, 2) for k, v in concept_nets.items()},
        "行业权重分": {k: round(v, 2) for k, v in industry_nets.items()},
        "概念窗口指标": {
            name: {
                "入选次数": int(concept_metrics[name]["入选次数"]),
                "综合涨幅": round(concept_metrics[name]["综合涨幅"], 2),
                "资金净流入": round(concept_metrics[name]["资金净流入"], 2),
            }
            for name in sorted(
                (n for n in concept_pool if n in concept_metrics),
                key=lambda n: (-concept_nets.get(n, 0), n),
            )[:12]
        },
        "权重分靠前": top,
    }
    dual = build_dual_window_score_breakdown(payload, state, mode=mode, section=BOARD_CONCEPT)
    if dual:
        out["双窗口"] = dual
    return out
