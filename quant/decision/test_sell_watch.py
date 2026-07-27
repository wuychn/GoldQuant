"""sell_watch 读写 + execute_intraday_sells 触发逻辑单测。"""

from __future__ import annotations

import pytest

from quant.decision.paper_execute import (
    execute_intraday_sells,
    read_sell_watch,
    write_sell_watch,
)


@pytest.fixture
def paper_home(tmp_path, monkeypatch):
    """临时 paper_account 根（name 必须是 paper_account，paper_home_context 才不嵌套）。"""
    from quant.store import paths

    paper = tmp_path / "paper_account"
    paper.mkdir()
    monkeypatch.setattr(paths, "_quant_home_override", paper)
    yield paper


def test_sell_watch_roundtrip(paper_home):
    rows = [
        {"code": "600000", "name": "浦发", "qty": 200, "hard_stop": 7.0, "force_sell": False}
    ]
    write_sell_watch("2026-07-28", rows)
    assert read_sell_watch("2026-07-28") == rows
    assert read_sell_watch("2026-07-29") is None


def test_execute_intraday_sells_break_hard_stop(paper_home):
    """现价 ≤ hard_stop → 触发卖出。"""
    write_sell_watch(
        "2026-07-28",
        [{"code": "600000", "name": "浦发", "qty": 200, "hard_stop": 7.5, "atr_stop": 7.6, "force_sell": False}],
    )
    spot = {"600000": {"close": 7.0, "pre_close": 8.0}}  # 7.0 ≤ 7.5
    result = execute_intraday_sells(read_sell_watch("2026-07-28"), spot, today="2026-07-28", dry_run=True)
    assert result["n_signals"] == 1


def test_execute_intraday_sells_break_atr_stop(paper_home):
    """现价 ≤ atr_stop（但 > hard_stop）→ 触发。"""
    write_sell_watch(
        "2026-07-28",
        [{"code": "600000", "name": "浦发", "qty": 200, "hard_stop": 7.0, "atr_stop": 7.3, "force_sell": False}],
    )
    spot = {"600000": {"close": 7.2, "pre_close": 7.6}}  # 7.2 > hard 7.0, ≤ atr 7.3
    result = execute_intraday_sells(read_sell_watch("2026-07-28"), spot, today="2026-07-28", dry_run=True)
    assert result["n_signals"] == 1


def test_execute_intraday_sells_no_trigger(paper_home):
    """现价高于所有 stop 且非 force → 不触发。"""
    write_sell_watch(
        "2026-07-28",
        [{"code": "600000", "name": "浦发", "qty": 200, "hard_stop": 7.0, "atr_stop": 6.9, "force_sell": False}],
    )
    spot = {"600000": {"close": 7.5, "pre_close": 7.6}}  # 7.5 > 7.0, 6.9
    result = execute_intraday_sells(read_sell_watch("2026-07-28"), spot, today="2026-07-28", dry_run=True)
    assert result["n_signals"] == 0


def test_execute_intraday_sells_force_sell(paper_home):
    """force_sell=True 无论价位 → 触发（T 晚判定的趋势/时间止损，次日开盘卖）。"""
    write_sell_watch(
        "2026-07-28",
        [{"code": "600000", "name": "浦发", "qty": 200, "hard_stop": 7.0, "atr_stop": 7.0, "force_sell": True, "reason": "trend_stop_ma20"}],
    )
    spot = {"600000": {"close": 10.0, "pre_close": 10.0}}  # 价高也卖
    result = execute_intraday_sells(read_sell_watch("2026-07-28"), spot, today="2026-07-28", dry_run=True)
    assert result["n_signals"] == 1


def test_execute_intraday_sells_skip_no_price(paper_home):
    """spot 无该票现价 → 跳过。"""
    write_sell_watch(
        "2026-07-28",
        [{"code": "600000", "name": "浦发", "qty": 200, "hard_stop": 7.0, "force_sell": False}],
    )
    result = execute_intraday_sells(read_sell_watch("2026-07-28"), {}, today="2026-07-28", dry_run=True)
    assert result["n_signals"] == 0
