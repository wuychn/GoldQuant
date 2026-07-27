"""Phase 3 风控门禁测试。"""

from __future__ import annotations

from quant.execution.risk_gate import assess_buy_gate, is_halt_active, trigger_halt, clear_halt


def test_healthy_allows_buy():
    dec = assess_buy_gate(
        total_assets=1_000_000, day_start_equity=1_000_000,
        index_change_pct=0.5, drawdown_from_peak_pct=-2.0,
        cooldown_codes=set(), sold_today_codes=set(),
        daily_loss_limit_pct=-3.0, circuit_breaker_pct=-2.0,
        max_drawdown_pct=15.0, halt_active=False, block_same_day_rebuy=True,
    )
    assert dec.allow_new_buy is True
    assert dec.blocked_codes == set()


def test_daily_loss_on_total_equity_blocks():
    """日内总权益回撤（含浮亏）≤ 限额 → 禁开仓。"""
    dec = assess_buy_gate(
        total_assets=960_000, day_start_equity=1_000_000,  # -4% ≤ -3%
        index_change_pct=0.0, drawdown_from_peak_pct=-4.0,
        cooldown_codes=set(), sold_today_codes=set(),
        daily_loss_limit_pct=-3.0, circuit_breaker_pct=-2.0,
        max_drawdown_pct=15.0, halt_active=False, block_same_day_rebuy=True,
    )
    assert dec.allow_new_buy is False
    assert any("日内亏损" in r for r in dec.reasons)


def test_circuit_breaker_blocks():
    dec = assess_buy_gate(
        total_assets=1_000_000, day_start_equity=1_000_000,
        index_change_pct=-2.5, drawdown_from_peak_pct=0.0,  # 大盘 -2.5% ≤ -2%
        cooldown_codes=set(), sold_today_codes=set(),
        daily_loss_limit_pct=-3.0, circuit_breaker_pct=-2.0,
        max_drawdown_pct=15.0, halt_active=False, block_same_day_rebuy=True,
    )
    assert dec.allow_new_buy is False
    assert any("大盘" in r for r in dec.reasons)


def test_drawdown_halt_blocks():
    dec = assess_buy_gate(
        total_assets=800_000, day_start_equity=1_000_000,
        index_change_pct=0.0, drawdown_from_peak_pct=-16.0,  # |16| ≥ 15
        cooldown_codes=set(), sold_today_codes=set(),
        daily_loss_limit_pct=-3.0, circuit_breaker_pct=-2.0,
        max_drawdown_pct=15.0, halt_active=False, block_same_day_rebuy=True,
    )
    assert dec.allow_new_buy is False
    assert any("回撤" in r for r in dec.reasons)


def test_cooldown_and_sold_codes_blacklisted_but_sells_allowed():
    """逐代码黑名单只禁买入；全局未触发时仍 allow_new_buy=True。"""
    dec = assess_buy_gate(
        total_assets=1_000_000, day_start_equity=1_000_000,
        index_change_pct=0.0, drawdown_from_peak_pct=0.0,
        cooldown_codes={"000001"}, sold_today_codes={"000002"},
        daily_loss_limit_pct=-3.0, circuit_breaker_pct=-2.0,
        max_drawdown_pct=15.0, halt_active=False, block_same_day_rebuy=True,
    )
    assert dec.allow_new_buy is True  # 卖出仍可（减仓不受限）
    assert dec.blocked_codes == {"000001", "000002"}


def test_block_same_day_rebuy_toggle():
    dec = assess_buy_gate(
        total_assets=1_000_000, day_start_equity=1_000_000,
        index_change_pct=0.0, drawdown_from_peak_pct=0.0,
        cooldown_codes=set(), sold_today_codes={"000001"},
        daily_loss_limit_pct=-3.0, circuit_breaker_pct=-2.0,
        max_drawdown_pct=15.0, halt_active=False, block_same_day_rebuy=False,
    )
    assert "000001" not in dec.blocked_codes  # 关闭同日回补限制


def test_halt_state_trigger_and_clear(monkeypatch):
    import quant.execution.risk_gate as rg
    from pathlib import Path

    fake = {"halt_until": "2099-12-31"}  # 远未来 → 活跃
    monkeypatch.setattr(rg, "_halt_path", lambda: Path("/nonexistent/risk_halt.json"))
    # 用真实可写临时路径
    import tempfile, json as _json
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "risk_halt.json"
        monkeypatch.setattr(rg, "_halt_path", lambda: p)
        assert is_halt_active() is False
        trigger_halt(halt_days=5, drawdown_pct=-16.0)
        assert is_halt_active() is True
        data = _json.loads(p.read_text(encoding="utf-8"))
        assert "halt_until" in data
        clear_halt()
        assert is_halt_active() is False
