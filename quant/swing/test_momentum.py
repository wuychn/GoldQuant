"""动量策略规则与纸面计划单测。"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from quant.swing.momentum import (
    MomentumConfig,
    build_momentum_plan,
    bump_idle,
    empty_slot_state,
    hs300_gate_on,
    occupy_slot,
    open_limit_up,
    rank_ret1_as_of,
    sell_due,
)


def _daily(n_days: int = 15, start: str = "2024-01-02") -> pd.DataFrame:
    d0 = date.fromisoformat(start)
    rows = []
    codes = [("600000", "浦发"), ("600001", "邯郸"), ("000001", "平安")]
    for i in range(n_days):
        dt = (d0 + timedelta(days=i)).isoformat()
        for j, (code, name) in enumerate(codes):
            close = 10.0 + i * 0.1 + j
            rows.append(
                {
                    "code": code,
                    "date": dt,
                    "name": name,
                    "open": close * 0.99,
                    "high": close * 1.01,
                    "low": close * 0.98,
                    "close": close,
                    "amount": 1e8,
                    "volume": 1e6,
                }
            )
    return pd.DataFrame(rows)


def test_hs300_gate_and_open_lu():
    s = pd.Series([1.0] * 40 + [1.2], index=range(41))
    assert hs300_gate_on(s, ma=20) is True
    s2 = pd.Series([1.2] * 40 + [1.0], index=range(41))
    assert hs300_gate_on(s2, ma=20) is False
    assert open_limit_up("600000", 11.0, 10.0) is True
    assert open_limit_up("600000", 10.2, 10.0) is False
    assert open_limit_up("300001", 12.0, 10.0) is True


def test_rank_ret1_prefers_winner():
    d = _daily(15)
    # 最后一天 000001 额外大涨
    last = d["date"].max()
    d.loc[(d["date"] == last) & (d["code"] == "000001"), "close"] = (
        float(d.loc[(d["date"] == last) & (d["code"] == "000001"), "close"].iloc[0]) * 1.05
    )
    cfg = MomentumConfig(topn=2, listed_days=5, min_adv=1.0)
    ranked = rank_ret1_as_of(d, last, cfg)
    assert ranked
    assert ranked[0]["code"] == "000001"


def test_plan_writes_pool_when_gate_on(monkeypatch):
    from quant.swing import momentum as m

    monkeypatch.setattr(m, "next_trading_day", lambda d: d + timedelta(days=1))
    d = _daily(15)
    as_of = d["date"].max()
    cfg = MomentumConfig(topn=2, listed_days=5, min_adv=1.0, hold_days=2, n_slots=2, max_idle=10)
    plan = build_momentum_plan(
        as_of, d, holdings=[], names={"000001": "平安"}, cfg=cfg, gate_on=True,
        slot_state=empty_slot_state(2),
    )
    assert plan.want_entry
    assert plan.slot_id == 0
    assert len(plan.battle_pool) >= 2
    assert plan.extra.get("strategy") == "momentum"
    assert plan.extra.get("buy_mode") == "open"


def test_plan_force_after_idle():
    from quant.swing import momentum as m
    from datetime import timedelta as td
    m.next_trading_day  # keep import path
    cfg = MomentumConfig(topn=2, listed_days=5, min_adv=1.0, max_idle=3, n_slots=2)
    state = empty_slot_state(2)
    state["idle"] = 3
    state["last_date"] = "2024-01-15"
    d = _daily(15)
    as_of = d["date"].max()
    plan = build_momentum_plan(
        as_of, d, holdings=[], cfg=cfg, slot_state=state, gate_on=False,
    )
    # bump_idle +1 because last_date != as_of and not invested → idle 4 >= 3
    assert plan.force
    assert plan.slot_scale == 0.5


def test_sell_due_and_orphan_watch(monkeypatch):
    from quant.swing import momentum as m

    monkeypatch.setattr(m, "next_trading_day", lambda d: d + timedelta(days=1))
    monkeypatch.setattr(m, "trading_days_between", lambda a, b: (b - a).days)
    assert sell_due("2024-01-02", "2024-01-04", 2) is True
    assert sell_due("2024-01-02", "2024-01-03", 2) is False
    d = _daily(15)
    as_of = d["date"].max()
    holdings = [{"股票代码": "600000", "持仓股数": 1000, "买入时间": "2024-01-02", "股票名称": "浦发"}]
    cfg = MomentumConfig(topn=2, listed_days=5, min_adv=1.0, hold_days=2)
    plan = build_momentum_plan(
        as_of, d, holdings, cfg=cfg, slot_state=empty_slot_state(2), gate_on=True,
    )
    sw = {s["code"]: s for s in plan.sell_watch}
    assert sw["600000"]["force_sell"] is True
    assert sw["600000"]["when"] == "close"


def test_occupy_and_idle_reset():
    st = empty_slot_state(2)
    st = occupy_slot(st, 0, ["600000"], "2024-01-10", 1.0)
    st = bump_idle(st, "2024-01-10", invested=True)
    assert st["idle"] == 0
    assert st["slots"][0]["codes"] == ["600000"]
