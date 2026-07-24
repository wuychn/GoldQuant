"""P2/P3：波动仓位、流动性、ablation、晋升、漂移动作测试。"""

from __future__ import annotations

from quant.execution.sim_rules import TradeSimConfig
from quant.execution.slippage import SlippageContext, effective_slippage_pct
from quant.ml.dataset import ScoreSample
from quant.monitoring.actions import apply_drift_actions, load_dimension_overrides, save_dimension_overrides
from quant.monitoring.paper_runner import compare_signals_vs_backtest
from quant.pool.liquidity import avg_daily_amount_yi, check_liquidity, filter_universe
from quant.portfolio.vol import atr_pct, realized_vol_pct, stock_volatility_pct
from quant.research.factor.ablation import dimension_ablation_ic
from quant.research.promote import evaluate_promotion_gates


def _hist_stock(n: int = 30, *, base: float = 10.0, amount: float = 2e8) -> dict:
    rows = []
    px = base
    for i in range(n):
        px = px * (1 + (0.01 if i % 2 == 0 else -0.008))
        rows.append(
            {
                "日期": f"2026-01-{i+1:02d}" if i < 28 else f"2026-02-{i-27:02d}",
                "收盘": round(px, 2),
                "最高": round(px * 1.02, 2),
                "最低": round(px * 0.98, 2),
                "成交额": amount,
            }
        )
    return {"股票代码": "600001", "历史行情": rows, "人气排名": 10}


def test_vol_and_atr():
    stock = _hist_stock()
    vol = realized_vol_pct(stock, lookback=10)
    atr = atr_pct(stock, lookback=10)
    assert vol is not None and vol > 0
    assert atr is not None and atr > 0
    assert stock_volatility_pct(stock) >= 1.0


def test_liquidity_adv_and_filter(monkeypatch):
    stock_ok = _hist_stock(amount=3e8)  # ~3亿
    stock_thin = _hist_stock(amount=1e6)  # 很小
    stock_thin["股票代码"] = "600002"
    monkeypatch.setattr(
        "quant.pool.liquidity._candidate_universe_cfg",
        lambda: {"universe": {"enabled": True, "min_adv_yi": 1.0, "adv_lookback": 20}},
    )
    ok, _ = check_liquidity(stock_ok)
    assert ok is True
    bad, reason = check_liquidity(stock_thin)
    assert bad is False
    assert "成交额" in reason
    filtered = filter_universe([stock_ok, stock_thin])
    assert len(filtered) == 1
    assert avg_daily_amount_yi(stock_ok) is not None


def test_crowding_filter(monkeypatch):
    stock = _hist_stock()
    stock["人气排名"] = 2
    monkeypatch.setattr(
        "quant.pool.liquidity._candidate_universe_cfg",
        lambda: {
            "universe": {
                "enabled": True,
                "min_adv_yi": 0,
                "max_crowding_rank": 3,
            }
        },
    )
    ok, reason = check_liquidity(stock)
    assert ok is False
    assert "拥挤" in reason


def test_adv_slippage_tiers():
    cfg = TradeSimConfig(slippage_model="vol_scaled", slippage_pct=0.001, slippage_max_pct=0.01)
    low = effective_slippage_pct(
        cfg,
        SlippageContext(volatility_pct=2.0, amount=1e5, adv_amount=1e9, participation=0.01),
    )
    high = effective_slippage_pct(
        cfg,
        SlippageContext(volatility_pct=2.0, amount=2e8, adv_amount=1e9, participation=0.2),
    )
    assert high > low


def test_dimension_ablation_ic():
    samples = []
    for d in ("2026-01-01", "2026-01-02"):
        for i in range(8):
            samples.append(
                ScoreSample(
                    date=d,
                    code=f"60000{i}",
                    name="t",
                    total=50 + i * 5,
                    dim_scores={"main_wave": 40 + i * 5, "technical": 30 + i},
                    label=1.0,
                    forward_return_pct=float(i - 3),
                )
            )
    out = dimension_ablation_ic(samples)
    assert "baseline" in out
    assert "main_wave" in out["ablations"]
    assert "delta_ic_mean" in out["ablations"]["main_wave"]


def test_promotion_gates_insufficient_samples():
    out = evaluate_promotion_gates(samples=[])
    assert out["passed"] is False


def test_dimension_overrides_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("quant.monitoring.actions.config_file", lambda name: tmp_path / name)
    monkeypatch.setattr("quant.monitoring.actions.ensure_layout", lambda: None)
    monkeypatch.setattr("quant.monitoring.actions.reload_config_cache", lambda: None)
    save_dimension_overrides(
        {
            "apply": True,
            "dimensions": {"technical": {"weight_factor": 0.5}},
        }
    )
    loaded = load_dimension_overrides()
    assert loaded["dimensions"]["technical"]["weight_factor"] == 0.5


def test_drift_actions_dry_run(monkeypatch):
    monkeypatch.setattr(
        "quant.monitoring.actions.detect_ic_drift",
        lambda **kwargs: {
            "alerts": [
                {"dim": "technical", "baseline_ic": 0.1, "recent_ic": -0.1, "type": "sign_flip"}
            ]
        },
    )
    out = apply_drift_actions(dry_run=True)
    assert len(out["actions"]) == 1
    assert out["actions"][0]["action"] == "demote"


def test_parity_compare():
    live = [{"code": "600001", "action": "买入"}, {"code": "600002", "action": "买入"}]
    bt = [{"code": "600001", "action": "买入"}, {"code": "600003", "action": "买入"}]
    from quant.monitoring import paper_runner as pr

    original = pr.load_daily_signals
    pr.load_daily_signals = lambda d: {"executable": live}
    try:
        r = pr.compare_signals_vs_backtest("2026-01-01", bt)
    finally:
        pr.load_daily_signals = original
    assert abs(r["parity_ratio"] - 1 / 3) < 1e-3
    assert any("600002" in x for x in r["only_live"])
    assert "600002" in r["only_live_codes"]
    assert r.get("field_level") is True