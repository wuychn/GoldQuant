"""组合风控与概念约束接线测试。"""

from __future__ import annotations

from quant.portfolio.constraints import check_concentration_constraints
from quant.portfolio.risk import (
    PortfolioRiskState,
    check_drawdown_halt,
    daily_loss_force_reduce,
    evaluate_live_drawdown_halt,
    load_risk_state,
    save_risk_state,
)
from quant.scoring.context import ScoreContext
from quant.research.factor.ic import daily_cross_sectional_rank_ic
from quant.ml.dataset import ScoreSample
from quant.ml.objective import proxy_sharpe_from_samples


def merge_dimension_overrides(base: dict, override: dict) -> dict:
    """与 scoring.engine 同逻辑（避免测试导入拉起 akshare）。"""
    if not override:
        return dict(base or {})
    out: dict = {k: (dict(v) if isinstance(v, dict) else v) for k, v in (base or {}).items()}
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = {**out[key], **val}
        else:
            out[key] = dict(val) if isinstance(val, dict) else val
    return out


def test_drawdown_halt_triggers():
    state = PortfolioRiskState(peak_equity=100_000)
    ok, reason = check_drawdown_halt(80_000, "2026-01-10", state)
    assert ok is False
    assert "回撤" in reason
    assert state.halt_until_date


def test_daily_loss_force_reduce():
    assert daily_loss_force_reduce(-5.0) is True
    assert daily_loss_force_reduce(-4.9) is False


def test_risk_state_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("quant.portfolio.risk.state_file", lambda name: tmp_path / name)
    monkeypatch.setattr("quant.portfolio.risk.ensure_layout", lambda: None)
    state = PortfolioRiskState(peak_equity=120_000, halt_until_date="2026-01-20")
    save_risk_state(state)
    loaded = load_risk_state()
    assert loaded.peak_equity == 120_000
    assert loaded.halt_until_date == "2026-01-20"


def test_live_drawdown_halt_persists(tmp_path, monkeypatch):
    monkeypatch.setattr("quant.portfolio.risk.state_file", lambda name: tmp_path / name)
    monkeypatch.setattr("quant.portfolio.risk.ensure_layout", lambda: None)
    monkeypatch.setattr("quant.store.state.get_total_assets", lambda: 100_000.0)
    ok1, _ = evaluate_live_drawdown_halt(equity=100_000, date_str="2026-01-01")
    assert ok1 is True
    ok2, reason = evaluate_live_drawdown_halt(equity=80_000, date_str="2026-01-02")
    assert ok2 is False
    assert "回撤" in reason


def test_concentration_blocks_overweight():
    ctx = ScoreContext.from_payload({})
    holdings = [
        {"股票代码": "600001", "概念": ["芯片"], "买入价": 10.0, "持仓股数": 4000},
    ]
    stock = {"股票代码": "600002", "概念": ["芯片"]}
    ok, reason = check_concentration_constraints(
        stock,
        ctx,
        holdings=holdings,
        proposed_value=20_000,
        total_assets=100_000,
    )
    assert ok is False
    assert "芯片" in reason


def test_merge_dimension_overrides():
    base = {"main_wave": {"enabled": True, "weight": 35}, "technical": {"enabled": True, "weight": 5}}
    over = {"main_wave": {"weight": 40}, "global_macro": {"enabled": False, "weight": 0}}
    merged = merge_dimension_overrides(base, over)
    assert merged["main_wave"]["enabled"] is True
    assert merged["main_wave"]["weight"] == 40
    assert merged["technical"]["weight"] == 5
    assert merged["global_macro"]["weight"] == 0


def test_daily_rank_ic_and_objective():
    samples = []
    for i in range(8):
        samples.append(
            ScoreSample(
                date="2026-01-01",
                code=f"60000{i}",
                name="t",
                total=50 + i * 5,
                dim_scores={"main_wave": 40 + i * 5},
                label=1.0 if i % 2 == 0 else 0.0,
                forward_return_pct=float(i - 3),
            )
        )
    for i in range(8):
        samples.append(
            ScoreSample(
                date="2026-01-02",
                code=f"60000{i}",
                name="t",
                total=55 + i * 4,
                dim_scores={"main_wave": 45 + i * 4},
                label=1.0,
                forward_return_pct=float(i - 2),
            )
        )
    report = daily_cross_sectional_rank_ic(samples)
    assert report["n_days"] == 2
    assert "ic_mean" in report
    sharpe = proxy_sharpe_from_samples(samples)
    assert isinstance(sharpe, float)
    # 无 forward_return 时不应用假收益
    bare = [
        ScoreSample("2026-01-01", "1", "a", 70, {}, 1.0),
        ScoreSample("2026-01-01", "2", "b", 60, {}, 0.0),
    ] * 6
    assert proxy_sharpe_from_samples(bare) == 0.0
