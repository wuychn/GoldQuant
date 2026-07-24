"""研究平台基础设施测试。"""

from __future__ import annotations

from quant.execution.sim_rules import TradeSimConfig
from quant.execution.slippage import SlippageContext, effective_slippage_pct
from quant.research.config_schema import validate_quant_config
from quant.research.registry import create_experiment, save_experiment
from quant.research.significance import bootstrap_sharpe
from quant.scoring.regime import _raw_regime_score


def test_validate_quant_config_ok():
    from quant.config import load_quant_config

    errors = validate_quant_config(load_quant_config())
    assert errors == []


def test_experiment_registry_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("quant.research.registry.QUANT_HOME", tmp_path)
    monkeypatch.setattr("quant.store.paths.QUANT_HOME", tmp_path)
    exp = create_experiment(hypothesis="test", data_from="2025-01-01", data_to="2025-01-31")
    path = save_experiment(exp, metrics={"total_return": 0.1})
    assert path.is_dir()
    assert (path / "metrics.json").is_file()


def test_bootstrap_sharpe():
    rets = [0.01, -0.005, 0.02, 0.0, -0.01] * 10
    out = bootstrap_sharpe(rets, n_samples=100, seed=1)
    assert "sharpe_ci_low" in out


def test_regime_v2_score():
    payload = {"大盘指数": [{"代码": "000001", "涨跌幅": 1.2}], "赚钱效应": {"上涨": 3000, "下跌": 1000}}
    assert _raw_regime_score(payload) >= 45


def test_slippage_vol_scaled():
    cfg = TradeSimConfig(slippage_model="vol_scaled", slippage_pct=0.001)
    slip = effective_slippage_pct(cfg, SlippageContext(volatility_pct=5.0, amount=100000))
    assert slip >= cfg.slippage_pct
