"""主线题材：滑动窗口概念榜快照 + 概念权重分 + 个股 concept_theme 打分。

概念权重（近 N 个交易日）：入选涨幅榜次数 50%、综合涨幅 30%、资金净流入 20%。
日快照与状态写入仅在 ``post_market_evening``；盘中/午间/盘前只读。
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


def _lookback_trading_days(cfg: dict[str, Any] | None = None) -> int:
    c = cfg or _theme_cfg()
    return max(1, int(c.get("lookback_days", 10)))


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


def collect_concept_window_metrics(
    state: dict[str, Any] | None = None,
    *,
    lookback: int | None = None,
) -> dict[str, dict[str, float]]:
    """近 N 个交易日概念原始指标：入选次数、综合涨幅(%)、资金净流入(亿元)。"""
    cfg = _theme_cfg()
    window = lookback if lookback is not None else _lookback_trading_days(cfg)
    st = state if state is not None else _load_state()
    window_dates = set(_trading_days_window(window))
    daily: dict[str, Any] = st.get("daily") or {}

    selection_count: dict[str, int] = defaultdict(int)
    composite_gain: dict[str, float] = defaultdict(float)
    net_fund: dict[str, float] = defaultdict(float)

    for d in sorted(window_dates):
        snap = daily.get(d)
        if not isinstance(snap, dict):
            continue
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

    universe = set(selection_count) | set(composite_gain) | set(net_fund)
    return {
        name: {
            "入选次数": float(selection_count.get(name, 0)),
            "综合涨幅": composite_gain.get(name, 0.0),
            "资金净流入": net_fund.get(name, 0.0),
        }
        for name in universe
    }


def _normalize_metric(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    mn = min(values.values())
    mx = max(values.values())
    if mx == mn:
        return {k: 50.0 for k in values}
    span = mx - mn
    return {k: 100.0 * (v - mn) / span for k, v in values.items()}


def build_concept_net_scores(
    state: dict[str, Any] | None = None,
    *,
    lookback: int | None = None,
) -> dict[str, float]:
    """近 N 个交易日概念权重分（0–100）：入选次数 50%、综合涨幅 30%、净流入 20%。"""
    cfg = _theme_cfg()
    window = lookback if lookback is not None else _lookback_trading_days(cfg)
    w_cfg = cfg.get("score_weights") or {}
    w_count = float(w_cfg.get("selection_count", 50))
    w_gain = float(w_cfg.get("composite_gain", 30))
    w_fund = float(w_cfg.get("net_fund_flow", 20))
    weight_sum = w_count + w_gain + w_fund
    if weight_sum <= 0:
        weight_sum = 100.0

    metrics = collect_concept_window_metrics(state, lookback=window)
    if not metrics:
        return {}

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


def resolve_main_theme_leaders(state: dict[str, Any], *, lookback: int | None = None) -> tuple[str | None, str | None]:
    """滑动窗口内：综合涨幅最大、资金净流入最大概念（各 1 条）。"""
    metrics = collect_concept_window_metrics(state, lookback=lookback)
    if not metrics:
        return None, None
    gain_totals = {k: v["综合涨幅"] for k, v in metrics.items()}
    fund_totals = {k: v["资金净流入"] for k, v in metrics.items()}
    return _max_by_total(gain_totals), _max_by_total(fund_totals)


def update_main_theme_state(payload: dict) -> dict[str, Any]:
    """按日写入概念四榜快照，保留滑动窗口。"""
    cfg = _theme_cfg()
    limit = int(cfg.get("board_limit", 10))
    lookback = _lookback_trading_days(cfg)
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
    """确认主线：近 N 交易日综合涨幅最大 + 资金净流入最多，最多 2 条。"""
    cfg = _theme_cfg()
    lookback = _lookback_trading_days(cfg)
    state = update_main_theme_state(payload) if update else _load_state()
    gain_main, fund_main = resolve_main_theme_leaders(state, lookback=lookback)
    out: set[str] = set()
    if gain_main:
        out.add(gain_main)
    if fund_main:
        out.add(fund_main)
    return out


def max_concept_net_score(stock_concepts: set[str], payload: dict | None = None) -> float:
    """个股概念集合在窗口权重分中的最高值（无匹配为 0）。"""
    del payload
    if not stock_concepts:
        return 0.0
    nets = build_concept_net_scores()
    matched = [nets[c] for c in stock_concepts if c in nets]
    return max(matched) if matched else 0.0


def concept_resonance_weights(payload: dict, *, update: bool = False) -> dict[str, float]:
    """兼容旧引用：返回滑动窗口概念权重分。"""
    del payload, update
    return build_concept_net_scores()


def score_concept_resonance(
    stock_concepts: set[str],
    payload: dict,
    *,
    update: bool = False,
) -> tuple[float, dict[str, Any]]:
    """个股 concept_theme：取命中概念中权重分最高者（0–100）；无命中为 0 分。"""
    del payload, update
    cfg = _theme_cfg()
    w_cfg = cfg.get("score_weights") or {}
    no_hit = float(w_cfg.get("no_hit", 0))

    nets = build_concept_net_scores()
    if not nets:
        return no_hit, {"available": False}

    matched = {c: nets[c] for c in stock_concepts if c in nets}
    if not matched:
        return no_hit, {"命中概念": [], "概念权重分": {}, "available": True}

    peak = max(matched.values())
    return max(0.0, min(100.0, peak)), {
        "available": True,
        "命中概念": sorted(matched.keys()),
        "概念权重分": {k: round(v, 2) for k, v in sorted(matched.items(), key=lambda x: -x[1])},
        "最高概念分": round(peak, 2),
    }


def theme_detail(payload: dict, *, update: bool = False) -> dict[str, Any]:
    """供评分维度输出的调试信息（默认不写入主线状态）。"""
    cfg = _theme_cfg()
    limit = int(cfg.get("board_limit", 10))
    lookback = _lookback_trading_days(cfg)
    gain, fund = snapshot_boards(payload, limit=limit)
    main = resolve_main_themes(payload, update=update)
    state = _load_state()
    gain_main, fund_main = resolve_main_theme_leaders(state, lookback=lookback)
    metrics = collect_concept_window_metrics(state, lookback=lookback)
    nets = build_concept_net_scores(state, lookback=lookback)
    top = sorted(nets.items(), key=lambda x: -x[1])[:8]
    return {
        "当日涨幅概念": sorted(gain),
        "当日资金概念": sorted(fund),
        "涨幅主线": gain_main,
        "资金主线": fund_main,
        "确认主线": sorted(main),
        "概念权重分": {k: round(v, 2) for k, v in nets.items()},
        "概念窗口指标": {
            k: {
                "入选次数": int(v["入选次数"]),
                "综合涨幅": round(v["综合涨幅"], 2),
                "资金净流入": round(v["资金净流入"], 2),
            }
            for k, v in sorted(metrics.items(), key=lambda x: -nets.get(x[0], 0))[:12]
        },
        "权重分靠前": top,
    }
