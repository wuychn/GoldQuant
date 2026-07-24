"""RegimeTracker / 连续仓位 / 显著性 / 部分成交 回归测试。"""

from __future__ import annotations

from unittest.mock import patch

from quant.execution.core import _cap_qty_by_participation, match_buy_order
from quant.execution.sim_rules import TradeSimConfig
from quant.research.significance import benjamini_hochberg, deflated_sharpe_ratio
from quant.scoring.context import ScoreContext
from quant.scoring.regime import (
    RegimeTracker,
    continuous_total_pct,
    reset_regime_tracker,
)
from quant.signals.models import TradeSignal


def test_regime_hysteresis_counts_by_day_not_call():
    reset_regime_tracker(RegimeTracker(hysteresis_days=2))
    tracker = reset_regime_tracker(RegimeTracker(hysteresis_days=2))
    # 强势 payload
    strong = {
        "赚钱效应": {"上涨": 3000, "下跌": 1000, "涨停": 90},
        "大盘指数": [{"代码": "000001", "涨跌幅": 1.5}],
        "涨停统计": {"市场高度": "5板", "今日涨停": [{}] * 90},
    }
    weak = {
        "赚钱效应": {"上涨": 500, "下跌": 3500, "涨停": 10},
        "大盘指数": [{"代码": "000001", "涨跌幅": -1.5}],
        "涨停统计": {"市场高度": "1板", "今日涨停": [{}] * 10},
    }
    assert tracker.update(strong, date_str="2026-01-01") == "震荡"
    # 同日多次不加速
    assert tracker.update(strong, date_str="2026-01-01") == "震荡"
    assert tracker.update(strong, date_str="2026-01-02") == "强势"
    # 切弱势需再累计
    assert tracker.update(weak, date_str="2026-01-03") == "强势"
    assert tracker.update(weak, date_str="2026-01-04") == "弱势"


def test_continuous_total_pct_range():
    assert continuous_total_pct(0) == 20.0
    assert continuous_total_pct(100) == 80.0
    assert abs(continuous_total_pct(50) - 50.0) < 1e-6


def test_position_limits_uses_tracker_and_continuous(monkeypatch):
    from quant.gates import rules as rules_mod

    tracker = reset_regime_tracker(RegimeTracker(hysteresis_days=1))
    strong = {
        "赚钱效应": {"上涨": 3000, "下跌": 1000, "涨停": 90},
        "大盘指数": [{"代码": "000001", "涨跌幅": 1.5}],
        "涨停统计": {"市场高度": "5板", "今日涨停": [{}] * 90},
    }
    tracker.update(strong, date_str="2026-01-10")
    monkeypatch.setattr(
        "quant.config.load_quant_config",
        lambda: {
            "portfolio": {
                "continuous_position": {"enabled": True, "min_pct": 20, "max_pct": 80}
            }
        },
    )
    monkeypatch.setattr(
        rules_mod,
        "load_gates_config",
        lambda: {
            "position": {
                "强势": {"total_pct": 80, "max_stocks": 5},
                "震荡": {"total_pct": 50, "max_stocks": 3},
                "弱势": {"total_pct": 20, "max_stocks": 2},
            }
        },
    )
    ctx = ScoreContext.from_payload(strong)
    limits = rules_mod.position_limits(ctx)
    assert limits["regime"] == "强势"
    assert 20 <= limits["total_pct"] <= 80
    assert limits["max_stocks"] == 5


def test_bh_fdr_and_dsr():
    fdr = benjamini_hochberg([0.001, 0.04, 0.5], alpha=0.05)
    assert fdr["n_tests"] == 3
    assert fdr["n_rejected"] >= 1
    dsr = deflated_sharpe_ratio(2.0, n_obs=252, n_trials=1)
    assert dsr["dsr"] >= 0.9


def test_partial_fill_caps_qty():
    sim = TradeSimConfig(partial_fill_enabled=True, participation_rate=0.1)
    # hist_rows_sorted 要求有收盘价
    stock = {
        "历史行情": [
            {"日期": f"2026-01-{i:02d}", "收盘": 10.0, "成交额": 1e8} for i in range(1, 26)
        ]
    }
    capped = _cap_qty_by_participation(10_000, 10.0, stock, sim)
    assert capped == 10_000
    capped2 = _cap_qty_by_participation(10_000_000, 10.0, stock, sim)
    assert 100 <= capped2 < 10_000_000
    assert capped2 % 100 == 0


@patch("quant.execution.core.at_limit_up_down", return_value=False)
@patch("quant.execution.core._time_ok", return_value=True)
def test_match_buy_partial_fill(_time, _lim):
    sim = TradeSimConfig(
        partial_fill_enabled=True,
        participation_rate=0.01,
        commission_rate=0.0001,
        min_commission=5.0,
        slippage_pct=0.0,
        slippage_model="fixed",
    )
    stock = {
        "历史行情": [
            {"日期": f"2026-01-{i:02d}", "收盘": 10.0, "成交额": 5e7} for i in range(1, 26)
        ]
    }
    sig = TradeSignal(
        action="买入",
        code="600000",
        name="浦发",
        price=10.0,
        quantity=100_000,
        strategy="主升浪战法",
        reason="测试",
        signal_kind="上升途中",
    )
    r = match_buy_order(
        sig,
        stock,
        cash=10_000_000,
        sold_today=False,
        already_held=False,
        enforce_hours=False,
        sim=sim,
    )
    assert r.ok
    assert r.quantity < 100_000
    assert r.quantity % 100 == 0


def test_industry_concentration_blocks():
    from quant.portfolio.constraints import check_concentration_constraints

    ctx = ScoreContext.from_payload({})
    holdings = [
        {
            "股票代码": "600001",
            "行业": "半导体",
            "买入价": 10.0,
            "持仓股数": 4000,
            "盘口": {"最新": 10.0},
        },
    ]
    stock = {"股票代码": "600002", "行业": "半导体", "概念": ["芯片"]}
    ok, reason = check_concentration_constraints(
        stock,
        ctx,
        holdings=holdings,
        proposed_value=20_000,
        total_assets=100_000,
    )
    assert ok is False
    assert "半导体" in reason
