"""截面 1 日动量：昨日强势 + 沪深300 均线门控 + 双槽 T+1 开/T+N 平。

回测（``momentum_bt``）与纸面（晚间计划 / 盘中开盘买、尾盘卖）共用本模块规则。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quant.config import load_quant_config
from quant.data.calendar import next_trading_day, trading_days_between
from quant.data.universe import _is_st_name
from quant.store.paths import state_file


def limit_up_th(code: str) -> float:
    """信号过滤用涨停近似阈值（创业板/科创 19.5%，其余 9.5%）。"""
    return 0.195 if str(code).startswith(("300", "301", "688")) else 0.095


def is_bj_code(code: str) -> bool:
    return str(code).startswith(("4", "8", "9"))


@dataclass(frozen=True)
class MomentumConfig:
    enabled: bool = True
    topn: int = 6
    ma: int = 55
    min_adv: float = 8e7
    hold_days: int = 2
    n_slots: int = 2
    max_idle: int = 10
    listed_days: int = 60
    index_code: str = "000300"
    candidate_mult: int = 6

    @classmethod
    def from_mapping(cls, raw: dict | None) -> MomentumConfig:
        raw = raw or {}
        return cls(
            enabled=bool(raw.get("enabled", True)),
            topn=int(raw.get("topn", 6)),
            ma=int(raw.get("ma", 55)),
            min_adv=float(raw.get("min_adv", 8e7)),
            hold_days=int(raw.get("hold_days", 2)),
            n_slots=int(raw.get("n_slots", 2)),
            max_idle=int(raw.get("max_idle", 10)),
            listed_days=int(raw.get("listed_days", 60)),
            index_code=str(raw.get("index_code") or "000300"),
            candidate_mult=int(raw.get("candidate_mult", 6)),
        )

    @classmethod
    def from_config(cls) -> MomentumConfig:
        return cls.from_mapping((load_quant_config().get("momentum_swing") or {}))

    @property
    def slot_weight(self) -> float:
        return 1.0 / max(self.n_slots, 1)


def momentum_enabled() -> bool:
    return MomentumConfig.from_config().enabled


def hs300_gate_on(close: pd.Series, *, ma: int) -> bool:
    """最新收盘是否高于 MA（信号日 T 判定，供 T+1 开仓）。"""
    s = pd.to_numeric(close, errors="coerce").dropna()
    if s.empty or ma <= 0:
        return False
    min_p = max(15, ma - 20)
    if len(s) < min_p:
        return False
    avg = float(s.rolling(ma, min_periods=min_p).mean().iloc[-1])
    last = float(s.iloc[-1])
    return bool(np.isfinite(last) and np.isfinite(avg) and last > avg)


def rank_lists(
    score: pd.DataFrame,
    valid: pd.DataFrame,
    dates: list[str],
    codes: list[str],
    *,
    n_keep: int = 40,
) -> dict[str, list[str]]:
    """score 越高越该买；invalid 置 -inf。回测与研究脚本共用。"""
    s = score.reindex(index=dates, columns=codes).to_numpy(dtype=float)
    v = valid.reindex(index=dates, columns=codes).to_numpy(dtype=bool)
    s = np.where(v & np.isfinite(s), s, -np.inf)
    out: dict[str, list[str]] = {}
    for i, dt in enumerate(dates):
        row = s[i]
        finite = np.isfinite(row)
        if int(finite.sum()) < 20:
            out[dt] = []
            continue
        k = min(n_keep, int(finite.sum()))
        part = np.argpartition(-row, k - 1)[:k]
        part = part[np.argsort(-row[part])]
        out[dt] = [codes[j] for j in part if np.isfinite(row[j])]
    return out


def pick_open_basket(
    ranks: dict[str, list[str]],
    sig_dt: str,
    entry_i: int,
    codes_keep: set[str],
    lu_open: pd.DataFrame,
    open_: pd.DataFrame,
    close: pd.DataFrame,
    max_gap: float | None,
    topn: int,
) -> list[str]:
    """T+1 开盘可买名单：跳过开盘涨停，可选跳过高开缺口。"""
    kept: list[str] = []
    for c in ranks.get(sig_dt, [])[: max(topn, 1) * 6]:
        if c not in codes_keep:
            continue
        if bool(lu_open.iloc[entry_i].get(c, False)):
            continue
        o = open_.iloc[entry_i][c]
        pc = close.iloc[entry_i - 1][c]
        if not np.isfinite(o) or not np.isfinite(pc) or o <= 0 or pc <= 0:
            continue
        if max_gap is not None and (o / pc - 1.0) > max_gap:
            continue
        kept.append(c)
        if len(kept) >= topn:
            break
    return kept


def open_limit_up(code: str, open_px: float, pre_close: float) -> bool:
    if not np.isfinite(open_px) or not np.isfinite(pre_close) or open_px <= 0 or pre_close <= 0:
        return True
    return (open_px / pre_close - 1.0) >= limit_up_th(code) * 0.98


# ---------- 纸面槽位状态 ----------


def _empty_slots(n_slots: int) -> list[dict]:
    return [
        {"id": i, "codes": [], "entry_date": None, "scale": 1.0, "pending": False}
        for i in range(n_slots)
    ]


def empty_slot_state(n_slots: int) -> dict:
    return {"idle": 0, "last_date": None, "slots": _empty_slots(n_slots)}


def load_slot_state(n_slots: int = 2) -> dict:
    path = state_file("mom_slots.json")
    if not path.is_file():
        return empty_slot_state(n_slots)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_slot_state(n_slots)
    slots = data.get("slots") or []
    if len(slots) < n_slots:
        slots = list(slots) + _empty_slots(n_slots - len(slots))
        for i, s in enumerate(slots):
            s["id"] = i
    data["slots"] = slots[:n_slots]
    data.setdefault("idle", 0)
    data.setdefault("last_date", None)
    return data


def save_slot_state(state: dict) -> Path:
    path = state_file("mom_slots.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def slot_has_position(slot: dict) -> bool:
    return bool(slot.get("codes")) or bool(slot.get("pending"))


def first_free_slot(state: dict) -> dict | None:
    for s in state.get("slots") or []:
        if not slot_has_position(s):
            return s
    return None


def invested_from_state(state: dict, holdings: list[dict] | None = None) -> bool:
    if any((s.get("codes") or []) for s in (state.get("slots") or [])):
        return True
    for h in holdings or []:
        try:
            qty = float(h.get("持仓股数") or h.get("股数") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        if qty > 0:
            return True
    return False


def bump_idle(state: dict, as_of: str, *, invested: bool) -> dict:
    """按决策日推进空仓计数（一天最多 +1）。"""
    if invested:
        state["idle"] = 0
        state["last_date"] = as_of
        return state
    if state.get("last_date") == as_of:
        return state
    state["idle"] = int(state.get("idle") or 0) + 1
    state["last_date"] = as_of
    return state


def occupy_slot(state: dict, slot_id: int, codes: list[str], entry_date: str, scale: float) -> dict:
    for s in state.get("slots") or []:
        if int(s.get("id", -1)) == int(slot_id):
            s["codes"] = [str(c) for c in codes]
            s["entry_date"] = entry_date
            s["scale"] = float(scale)
            s["pending"] = False
            break
    return state


def mark_slot_pending(state: dict, slot_id: int, scale: float) -> dict:
    for s in state.get("slots") or []:
        if int(s.get("id", -1)) == int(slot_id):
            s["pending"] = True
            s["scale"] = float(scale)
            break
    return state


def clear_pending_unfilled(state: dict) -> dict:
    """晚间：昨日 pending 但未成交的槽位释放。"""
    for s in state.get("slots") or []:
        if s.get("pending") and not (s.get("codes") or []):
            s["pending"] = False
            s["entry_date"] = None
            s["scale"] = 1.0
    return state


def release_sold_codes(state: dict, sold: list[str]) -> dict:
    gone = {str(c) for c in sold}
    if not gone:
        return state
    for s in state.get("slots") or []:
        codes = [c for c in (s.get("codes") or []) if str(c) not in gone]
        s["codes"] = codes
        if not codes and not s.get("pending"):
            s["entry_date"] = None
            s["scale"] = 1.0
    return state


def sync_slots_with_holdings(state: dict, holdings: list[dict]) -> dict:
    held = set()
    for h in holdings:
        code = str(h.get("股票代码") or "").strip()
        try:
            qty = float(h.get("持仓股数") or h.get("股数") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        if code and qty > 0:
            held.add(code)
    for s in state.get("slots") or []:
        codes = [c for c in (s.get("codes") or []) if c in held]
        s["codes"] = codes
        if not codes and not s.get("pending"):
            s["entry_date"] = None
    return state


def slot_codes(state: dict) -> set[str]:
    out: set[str] = set()
    for s in state.get("slots") or []:
        out.update(str(c) for c in (s.get("codes") or []))
    return out


def sell_due(entry_date: str | None, target_date: str, hold_days: int) -> bool:
    if not entry_date or hold_days <= 0:
        return False
    try:
        a = date.fromisoformat(str(entry_date)[:10])
        b = date.fromisoformat(str(target_date)[:10])
    except ValueError:
        return False
    return trading_days_between(a, b) >= hold_days


# ---------- 信号日选股 ----------


def _prepare_daily(daily: pd.DataFrame) -> pd.DataFrame:
    d = daily.copy()
    d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
    d["code"] = d["code"].astype(str)
    if "name" not in d.columns:
        d["name"] = ""
    if "amount" not in d.columns:
        d["amount"] = 0.0
    return d


def rank_ret1_as_of(daily: pd.DataFrame, as_of: str, cfg: MomentumConfig) -> list[dict[str, Any]]:
    """信号日 T 的可交易昨日涨幅排序（高→低）。"""
    d = _prepare_daily(daily)
    d = d[d["date"] <= as_of]
    if d.empty:
        return []
    d = d[~d["code"].map(is_bj_code)]
    if "name" in d.columns:
        last_name = d.groupby("code")["name"].last()
        st = {str(c) for c, n in last_name.items() if _is_st_name(str(n))}
        d = d[~d["code"].isin(st)]
    if d.empty:
        return []

    close = d.pivot_table(index="date", columns="code", values="close", aggfunc="last")
    amount = d.pivot_table(index="date", columns="code", values="amount", aggfunc="last")
    if as_of not in close.index:
        return []
    codes = [str(c) for c in close.columns]
    close.columns = amount.columns = codes
    ret = close.pct_change()
    adv = amount.rolling(20, min_periods=10).mean()
    listed = close.notna().astype(float).cumsum()
    th = pd.Series({c: limit_up_th(c) for c in codes})
    row_close = close.loc[as_of]
    row_ret = ret.loc[as_of] if as_of in ret.index else pd.Series(dtype=float)
    row_adv = adv.loc[as_of] if as_of in adv.index else pd.Series(dtype=float)
    row_listed = listed.loc[as_of]
    tradable = (
        (row_adv >= cfg.min_adv)
        & row_close.notna()
        & (row_listed >= cfg.listed_days)
        & ~row_ret.ge(th).fillna(False)
        & row_ret.notna()
    )
    names = d.groupby("code")["name"].last()
    scored = row_ret.where(tradable)
    ranked = scored.dropna().sort_values(ascending=False)
    n_keep = max(cfg.topn * cfg.candidate_mult, cfg.topn)
    out: list[dict[str, Any]] = []
    for i, (code, ret1) in enumerate(ranked.head(n_keep).items()):
        out.append(
            {
                "code": str(code),
                "name": str(names.get(code) or code),
                "ret1": float(ret1),
                "rank": i + 1,
            }
        )
    return out


def read_hs300_gate(as_of: str, cfg: MomentumConfig) -> tuple[bool, float | None, float | None]:
    from quant.data.store import read_index_daily

    try:
        idx = read_index_daily(cfg.index_code, end=as_of)
    except Exception:
        return False, None, None
    if idx is None or idx.empty:
        return False, None, None
    idx = idx.copy()
    idx["date"] = pd.to_datetime(idx["date"]).dt.strftime("%Y-%m-%d")
    s = idx.drop_duplicates("date").set_index("date")["close"].astype(float)
    s = s[s.index <= as_of]
    if s.empty:
        return False, None, None
    last = float(s.iloc[-1])
    min_p = max(15, cfg.ma - 20)
    ma = s.rolling(cfg.ma, min_periods=min_p).mean()
    ma_last = float(ma.iloc[-1]) if len(ma) else float("nan")
    on = bool(np.isfinite(last) and np.isfinite(ma_last) and last > ma_last)
    return on, last, ma_last if np.isfinite(ma_last) else None


@dataclass
class MomentumPlan:
    as_of: str
    target_date: str
    gate_on: bool
    force: bool
    idle: int
    hs300: float | None
    hs300_ma: float | None
    want_entry: bool
    slot_id: int | None
    slot_scale: float
    candidates: list[dict]
    battle_pool: list[dict]
    sell_watch: list[dict]
    skip_reason: str = ""
    slot_state: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)


def _holding_qty(h: dict) -> int:
    try:
        return int(float(h.get("持仓股数") or h.get("股数") or 0))
    except (TypeError, ValueError):
        return 0


def build_momentum_plan(
    as_of: str,
    daily: pd.DataFrame,
    holdings: list[dict],
    names: dict[str, str] | None = None,
    *,
    cfg: MomentumConfig | None = None,
    slot_state: dict | None = None,
    gate_on: bool | None = None,
) -> MomentumPlan:
    """T 晚：写 T+1 作战池（开盘买）与卖出监控（到期尾盘卖）。"""
    cfg = cfg or MomentumConfig.from_config()
    nxt = next_trading_day(date.fromisoformat(as_of))
    target_date = nxt.isoformat() if nxt else as_of
    names = names or {}

    state = slot_state if slot_state is not None else load_slot_state(cfg.n_slots)
    state = clear_pending_unfilled(state)
    state = sync_slots_with_holdings(state, holdings)
    invested = invested_from_state(state, holdings)
    state = bump_idle(state, as_of, invested=invested)
    idle = int(state.get("idle") or 0)

    if gate_on is None:
        gate_on, hs300, hs300_ma = read_hs300_gate(as_of, cfg)
    else:
        hs300, hs300_ma = None, None

    force = (not gate_on) and cfg.max_idle > 0 and idle >= cfg.max_idle
    want = gate_on or force
    scale = 1.0 if gate_on else (0.5 if force else 0.0)
    free = first_free_slot(state) if want and scale > 0 else None

    candidates: list[dict] = []
    battle_pool: list[dict] = []
    slot_id: int | None = None
    skip = ""
    if want and free is None:
        skip = "双槽均占用"
    elif want and free is not None:
        candidates = rank_ret1_as_of(daily, as_of, cfg)
        min_n = max(1, (cfg.topn + 1) // 2)
        if len(candidates) < min_n:
            skip = "可交易候选不足"
        else:
            slot_id = int(free["id"])
            slot_w = cfg.slot_weight * scale
            per = slot_w / max(cfg.topn, 1)
            for i, row in enumerate(candidates):
                tw = per if i < cfg.topn else 0.0
                battle_pool.append(
                    {
                        "code": row["code"],
                        "name": row["name"],
                        "alpha": round(float(row["ret1"]), 4),
                        "rank": row["rank"],
                        "target_weight": round(float(tw), 4),
                        "why": f"昨日涨幅Top rank={row['rank']}",
                    }
                )
            mark_slot_pending(state, slot_id, scale)
    elif not want:
        skip = f"门控关且空仓{idle}日<{cfg.max_idle}"

    sell_watch: list[dict] = []
    tagged = slot_codes(state)
    for h in holdings:
        code = str(h.get("股票代码") or "").strip()
        qty = _holding_qty(h)
        if not code or qty <= 0:
            continue
        buy_date = str(h.get("买入时间") or "")[:10]
        name = names.get(code) or str(h.get("股票名称") or code)
        in_slot = False
        entry = buy_date
        for s in state.get("slots") or []:
            if code in (s.get("codes") or []):
                in_slot = True
                entry = str(s.get("entry_date") or buy_date)
                break
        due = sell_due(entry, target_date, cfg.hold_days)
        reason = ""
        force_sell = False
        when = ""
        if due:
            force_sell = True
            reason = "hold_expiry"
            when = "close"
        elif not in_slot:
            force_sell = True
            reason = "orphan"
            when = "close"
        sell_watch.append(
            {
                "code": code,
                "name": name,
                "qty": qty,
                "entry": None,
                "force_sell": force_sell,
                "reason": reason,
                "when": when,
                "hard_stop": None,
                "atr_stop": None,
            }
        )

    extra = {
        "strategy": "momentum",
        "buy_mode": "open",
        "fill_n": cfg.topn,
        "slot_id": slot_id,
        "slot_scale": scale,
        "n_slots": cfg.n_slots,
        "hold_days": cfg.hold_days,
        "gate_on": gate_on,
        "force": force,
        "idle": idle,
    }
    return MomentumPlan(
        as_of=as_of,
        target_date=target_date,
        gate_on=gate_on,
        force=force,
        idle=idle,
        hs300=hs300,
        hs300_ma=hs300_ma,
        want_entry=bool(want and slot_id is not None),
        slot_id=slot_id,
        slot_scale=scale,
        candidates=candidates,
        battle_pool=battle_pool,
        sell_watch=sell_watch,
        skip_reason=skip,
        slot_state=state,
        extra=extra,
    )
