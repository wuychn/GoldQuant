"""P6 辅助决策闭环单元测试。"""

from __future__ import annotations

from quant.decision.daily_output import build_decision_card, card_to_text
from quant.journal.deviation import DeviationJournal
from quant.journal.funnel import FunnelTracker


def test_decision_card_actions():
    alpha = {"000001": 1.0, "000002": 0.8, "000003": 0.5, "000004": 0.3}
    target = {"000001": 0.3, "000002": 0.3, "000003": 0.3}
    current = {"000002": 0.295, "000004": 0.3}  # 000004 不在目标，应卖；000002 偏离 0.005 在阈值内
    card = build_decision_card("20240101", alpha, target, current, trade_threshold=0.01)
    sides = {a.code: a.side for a in card.actions}
    assert sides["000001"] == "buy"  # 新建仓
    assert sides["000004"] == "sell"  # 出目标
    assert sides["000003"] == "buy"
    # 000002 偏离 0.05 在阈值内 → hold
    assert sides["000002"] == "hold"
    txt = card_to_text(card)
    assert "决策卡" in txt


def test_exit_signal_forces_sell():
    alpha = {"000001": 1.0}
    target = {"000001": 0.3}
    current = {"000001": 0.3}
    exits = [{"code": "000001", "reason": "atr_trailing", "price": 9.5}]
    card = build_decision_card("20240101", alpha, target, current, exit_signals=exits)
    a = next(x for x in card.actions if x.code == "000001")
    assert a.side == "sell" and a.reason == "exit_signal"


def test_deviation_journal_summary():
    alpha = {"000001": 1.0, "000002": 0.5}
    target = {"000001": 0.5, "000002": 0.5}
    current = {"000001": 0.0, "000002": 0.5}
    card = build_decision_card("20240101", alpha, target, current)
    j = DeviationJournal()
    # 实际：000001 没买（建议 buy），000002 持有
    j.record_from_card(card, actual={"000001": ("hold", 0.0), "000002": ("hold", 0.5)})
    s = j.summary()
    assert s["n"] > 0
    assert s["obey_rate"] < 1.0  # 有偏离


def test_funnel_tracker_summary():
    ft = FunnelTracker()
    ft.observe_universe("20240101", ["000001", "000002", "000003", "000004"])
    ft.observe_target("20240101", ["000001", "000002"])
    ft.observe_buy("20240101", "000001")
    ft.record_outcome("20240101", "000001", held_days=5, pnl_pct=3.2)
    s = ft.summary()
    assert s["n_universe"] == 4
    assert s["n_target"] == 2
    assert s["n_bought"] == 1
    assert s["conv_universe_to_target"] == 0.5
    assert s["conv_target_to_buy"] == 0.5
    assert s["win_rate"] == 1.0
    assert s["avg_pnl_pct"] == 3.2
